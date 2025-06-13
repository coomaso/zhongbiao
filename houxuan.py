import json
import requests
import datetime
import os
import re
import time
from bs4 import BeautifulSoup
from typing import List, Dict, Any

class BidMonitor:
    def __init__(self):
        # 初始化文件路径
        self.original_file = "hx.json"
        self.parsed_file = "hx_parsed.json"
        
        # 企业微信配置
        self.webhook_url = os.getenv("QYWX_URL")
        self.webhook_zb_url = os.getenv("QYWX_ZB_URL")
        
        # 检查环境变量
        if not self.webhook_url:
            print("警告：QYWX_URL环境变量未设置，无法发送常规通知")
        if not self.webhook_zb_url:
            print("警告：QYWX_ZB_URL环境变量未设置，无法发送中标特别通知")
        
        # API配置
        self.api_url = "https://ggzy.sc.yichang.gov.cn/EpointWebBuilder/rest/secaction/getSecInfoListYzm"
        self.site_guid = "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
        self.category_num = "003001004"  # 中标候选人类别
        self.page_size = 6
        self.latest_new_count = 0  # 跟踪最新新增数量
        self.base_url = "https://ggzy.sc.yichang.gov.cn"  # 基础URL
        
    def _load_json_file(self, filename: str) -> List[Dict]:
        """加载JSON文件"""
        try:
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except Exception as e:
            print(f"[文件错误] 加载 {filename} 失败: {str(e)}")
            return []

    def _save_json_file(self, filename: str, data: List[Dict]):
        """保存JSON文件"""
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[文件错误] 保存 {filename} 失败: {str(e)}")

    def _is_existing_record(self, new_item: Dict, existing: List[Dict]) -> bool:
        """检查记录是否已存在"""
        new_id = new_item.get("infoid")
        new_url = new_item.get("infourl")
        return any(
            item.get("infoid") == new_id or 
            item.get("infourl") == new_url
            for item in existing
        )
    
    def reparse_all_data(self):
        """重新解析所有原始数据"""
        original_data = self._load_json_file(self.original_file)
        parsed_data = []
        
        for item in original_data:
            parsed_record = {
                "infoid": item.get("infoid"),
                "infourl": item.get("infourl"),
                "parsed_data": self._parse_html_content(item),
                "raw_data": {
                    "title": item.get("title"),
                    "infodate": item.get("infodate")
                }
            }
            parsed_data.append(parsed_record)
        
        self._save_json_file(self.parsed_file, parsed_data)
        print(f"[重解析完成] 共解析 {len(parsed_data)} 条数据并保存到 {self.parsed_file}")

    def fetch_latest_data(self) -> List[Dict]:
        """获取最新招标数据"""
        payload = {
            "siteGuid": self.site_guid,
            "categoryNum": self.category_num,
            "pageindex": "0",
            "pagesize": str(self.page_size),
            "content": "",
            "startdate": "",
            "enddate": "", 
            "xiqucode": ""
        }
        
        for attempt in range(3):
            try:
                response = requests.post(self.api_url, data=payload, timeout=30)
                response.raise_for_status()
                return response.json().get("custom", {}).get("infodata", [])
            except requests.exceptions.Timeout:
                print(f"[第 {attempt+1} 次尝试] 请求超时")
            except requests.RequestException as e:
                print(f"[第 {attempt+1} 次尝试] 请求失败: {str(e)}")
            time.sleep(5)
        
        print("[最终失败] 无法获取数据")
        return []

    def process_and_store_data(self) -> int:
        """处理并存储数据"""
        new_raw_data = self.fetch_latest_data()
        if not new_raw_data:
            return 0

        existing_raw = self._load_json_file(self.original_file)
        new_items = [
            item for item in new_raw_data
            if not self._is_existing_record(item, existing_raw)
        ]
        
        if not new_items:
            return 0

        # 保存原始数据
        updated_raw = existing_raw + new_items
        self._save_json_file(self.original_file, updated_raw)
        
        # 解析新数据
        parsed_data = self._load_json_file(self.parsed_file)
        for item in new_items:
            parsed_record = {
                "infoid": item.get("infoid"),
                "infourl": item.get("infourl"),
                "parsed_data": self._parse_html_content(item),
                "raw_data": {
                    "title": item.get("title"),
                    "infodate": item.get("infodate")
                }
            }
            parsed_data.append(parsed_record)
        
        self._save_json_file(self.parsed_file, parsed_data)
        self.latest_new_count = len(new_items)  # 保存最新数量
        return self.latest_new_count

    def _parse_html_content(self, data: Dict) -> Dict:
        """解析HTML内容，提取关键信息"""
        try:
            # 提取项目名称
            project_name = data.get("customtitle", "").replace("中标候选人公示", "").strip()
            
            # 解析HTML内容
            infocontent = data.get("infocontent", "")
            soup = BeautifulSoup(infocontent, 'html.parser')
            
            # 提取公示时间
            publicity_period = ""
            pub_time_patterns = [
                r"公示[期时为](.+?至.+?)(?:\n|<)",
                r"公示[期时为](.+?)(?:\n|<)",
                r"公示时间[：:](.+?至.+?)(?:\n|<)",
                r"公示期[：:](.+?至.+?)(?:\n|<)"
            ]
            
            full_text = soup.get_text()
            for pattern in pub_time_patterns:
                match = re.search(pattern, full_text)
                if match:
                    publicity_period = match.group(1).strip()
                    break
            
            # 提取中标候选人及报价 - 优化后的逻辑
            bidders_and_prices = []
            
            # 1. 尝试从表格中提取候选人
            candidate_tables = []
            for table in soup.find_all('table'):
                headers = [th.get_text(strip=True) for th in table.find_all(['th', 'td'])]
                
                # 检查是否包含候选人相关表头
                if any(keyword in header for header in headers for keyword in ["中标候选人", "投标人", "单位名称"]):
                    candidate_tables.append(table)
            
            # 处理找到的候选人表格
            for table in candidate_tables:
                rows = table.find_all('tr')
                if not rows:
                    continue
                
                # 查找包含候选人的行
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    cell_texts = [cell.get_text(strip=True) for cell in cells]
                    
                    # 跳过表头行
                    if any(keyword in cell_text for keyword in ["中标候选人", "投标人", "单位名称"] for cell_text in cell_texts):
                        continue
                    
                    # 尝试识别候选人名称
                    candidate_name = ""
                    for text in cell_texts:
                        if "公司" in text or "集团" in text or "院" in text:
                            candidate_name = text
                            break
                    
                    # 尝试识别报价
                    bid_price = ""
                    for text in cell_texts:
                        if "元" in text or "万" in text or "%" in text or "下浮" in text:
                            bid_price = text
                            break
                    
                    # 如果找到候选人，添加到列表
                    if candidate_name:
                        bidders_and_prices.append({
                            "bidder": candidate_name,
                            "price": bid_price if bid_price else "未提供"
                        })
            
            # 2. 如果表格方法未找到候选人，尝试文本匹配
            if not bidders_and_prices:
                # 尝试匹配公司名称
                company_pattern = r'([\u4e00-\u9fa5]{5,}(?:公司|集团|设计院|研究院|工程局))'
                companies = re.findall(company_pattern, full_text)
                unique_companies = list(dict.fromkeys(companies))  # 去重保留顺序
                
                # 尝试匹配报价
                price_pattern = r'(?:投标报价|报价|下浮率)[:：\s]*([\d.,%元万]+)|([\d.,]+)\s*(?:元|万元|%)'
                prices = [match[0] or match[1] for match in re.findall(price_pattern, full_text)]
                
                # 组合候选人和报价
                for i, company in enumerate(unique_companies):
                    price = prices[i] if i < len(prices) else "未提供"
                    bidders_and_prices.append({
                        "bidder": company,
                        "price": price
                    })
            
            # 3. 如果仍然没有找到候选人，使用默认提示
            if not bidders_and_prices:
                bidders_and_prices.append({
                    "bidder": "未能提取中标候选人",
                    "price": "请查看详情链接"
                })
            
            # 确保至少有3个候选人（按样本数据格式）
            while len(bidders_and_prices) < 3:
                bidders_and_prices.append({
                    "bidder": "未提供",
                    "price": "未提供"
                })
            
            # 构建完整URL
            infourl = data.get("infourl", "")
            full_url = f"{self.base_url}{infourl}" if infourl and infourl.startswith("/") else infourl
            
            return {
                "project_name": project_name,
                "publicity_period": publicity_period,
                "bidders_and_prices": bidders_and_prices[:3],  # 只取前3名候选人
                "full_url": full_url
            }
        except Exception as e:
            print(f"[解析错误] 解析HTML内容失败: {str(e)}")
            return {
                "project_name": "解析失败",
                "publicity_period": "",
                "bidders_and_prices": [],
                "full_url": ""
            }

    def _build_message(self, record: Dict) -> str:
        """构建通知消息"""
        try:
            parsed_data = record.get("parsed_data", {})
            raw_data = record.get("raw_data", {})
            
            # 构建中标候选人表格
            markdown_table = ""
            bap = parsed_data.get("bidders_and_prices", [])
            
            if bap:
                table_header = "| 序号 | 中标候选人 | 投标报价 |\n| :----- | :----: | -------: |"
                table_rows = []
                
                for i, item in enumerate(bap):
                    bidder = item.get("bidder", "未提供")
                    price = item.get("price", "未提供")
                    
                    # 格式化报价
                    formatted_price = price
                    if '%' in price:
                        formatted_price = f"下浮率: {price}"
                    elif any(char.isdigit() for char in price): 
                        clean_price_str = re.sub(r'[^\d.]', '', price.replace(',', ''))
                        if clean_price_str:
                            try:
                                num_price = float(clean_price_str)
                                if "万元" in price or num_price > 10000:
                                    formatted_price = f"{num_price:,.2f}万元"
                                else:
                                    formatted_price = f"{num_price:,.2f}元"
                            except ValueError:
                                pass  # 保持原格式
                    
                    table_rows.append(f"| {i+1} | {bidder} | {formatted_price} |")
                
                markdown_table = table_header + "\n" + "\n".join(table_rows)
            
            # 构建完整消息
            message = (
                "**📢 中标候选人公告**\n"
                f"📜 标题：{raw_data.get('title', '未知标题')}\n"
                f"📅 日期：{raw_data.get('infodate', '未知日期')}\n"
                f"⏳ 公示时间：{parsed_data.get('publicity_period', '未提供')}\n\n"
            )
            
            if markdown_table:
                message += "**🏆 中标候选人及报价：**\n" + markdown_table + "\n\n"
            else:
                message += "**🏆 中标候选人：**\n"
                for i, item in enumerate(bap):
                    bidder = item.get("bidder", "未提供")
                    price = item.get("price", "未提供")
                    message += f"{i+1}. {bidder}"
                    if price and price != "未提供":
                        message += f" (报价: {price})"
                    message += "\n"
                message += "\n"
            
            message += f"🔗 详情链接：{parsed_data.get('full_url', '')}"
            
            return message
        except Exception as e:
            print(f"[消息构建错误] 构建通知消息失败: {str(e)}")
            return ""

    def send_notifications(self):
        """发送通知"""
        if self.latest_new_count <= 0:
            return

        parsed_data = self._load_json_file(self.parsed_file)
        # 确保只处理当前新增的数据
        latest_parsed = parsed_data[-self.latest_new_count:]
        
        for record in latest_parsed:
            message = self._build_message(record)
            if not message:
                continue

            # 常规通知
            if self.webhook_url:
                self._send_wechat(message, self.webhook_url)
            
            # 检查是否有"盛荣"中标
            if "盛荣" in message:
                # 中标特别通知
                if self.webhook_zb_url:
                    self._send_wechat(f"【入围投标候选人通知】\n{message}", self.webhook_zb_url)

    def _send_wechat(self, message: str, webhook: str):
        """发送企业微信通知"""
        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": message
                }
            }
            response = requests.post(webhook, json=payload, timeout=10)
            response.raise_for_status()
            print(f"[通知成功] 发送到 {webhook}")
        except Exception as e:
            print(f"[通知失败] {str(e)}")

if __name__ == "__main__":
    import sys
    monitor = BidMonitor()

    if "--reparse-all" in sys.argv:
        monitor.reparse_all_data()
    else:
        new_count = monitor.process_and_store_data()
        if new_count > 0:
            print(f"发现 {new_count} 条新公告")
            monitor.send_notifications()
        else:
            print("没有新数据需要处理")
