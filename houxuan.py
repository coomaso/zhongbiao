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
            # 尝试多种方式定位公示时间
            pub_time_patterns = [
                r"公示[期时为](.+?至.+?)\n",
                r"公示[期时为](.+?)\n",
                r"公示时间[：:](.+?至.+?)\n",
                r"公示期[：:](.+?至.+?)\n"
            ]
            
            full_text = soup.get_text()
            for pattern in pub_time_patterns:
                match = re.search(pattern, full_text)
                if match:
                    publicity_period = match.group(1).strip()
                    break
            
            # 提取中标候选人及报价
            bidders = []
            prices = []
            
            # 改进的表格解析逻辑
            for table in soup.find_all('table'):
                # 尝试识别表头行
                header_row = None
                for row in table.find_all('tr'):
                    # 检查是否包含"中标候选人"、"投标人"等关键词
                    row_text = row.get_text()
                    if "中标候选人" in row_text or "投标人" in row_text or "报价" in row_text:
                        header_row = row
                        break
                
                if header_row:
                    # 确定列位置
                    header_cells = [td.get_text(strip=True) for td in header_row.find_all(['th', 'td'])]
                    
                    # 确定投标人名称列
                    bidder_col = None
                    for i, text in enumerate(header_cells):
                        if "候选人" in text or "投标人" in text or "单位名称" in text:
                            bidder_col = i
                            break
                    
                    # 确定报价列
                    price_col = None
                    for i, text in enumerate(header_cells):
                        if "报价" in text or "金额" in text or "下浮率" in text:
                            price_col = i
                            break
                    
                    # 如果找到有效列，提取数据
                    if bidder_col is not None or price_col is not None:
                        # 处理后续数据行
                        for row in header_row.find_next_siblings('tr'):
                            cells = row.find_all(['td'])
                            if len(cells) > max(bidder_col or 0, price_col or 0):
                                # 提取投标人
                                if bidder_col is not None and bidder_col < len(cells):
                                    bidder = cells[bidder_col].get_text(strip=True)
                                    # 有效性过滤
                                    if len(bidder) > 2 and not any(keyword in bidder for keyword in 
                                                                ["下浮率", "质量", "目标", "设计", "施工", "标准"]):
                                        bidders.append(bidder)
                                
                                # 提取报价
                                if price_col is not None and price_col < len(cells):
                                    price = cells[price_col].get_text(strip=True)
                                    # 有效性过滤
                                    if any(char in price for char in ["元", "%", ".", "万"]) and len(price) < 20:
                                        prices.append(price)
            
            # 备用方案1：尝试从文本中提取候选人
            if not bidders:
                # 尝试匹配候选人列表格式
                candidate_matches = re.findall(r'第[一二三四五]名[：:]\s*([^\n（]+)', full_text)
                if candidate_matches:
                    bidders = [match.strip() for match in candidate_matches]
                
                # 尝试匹配公司名称格式
                if not bidders:
                    company_matches = re.findall(r'[（(](\w{5,}公司|\w{5,}有限公司|\w{5,}集团)', full_text)
                    if company_matches:
                        bidders = list(set(company_matches))  # 去重
            
            # 备用方案2：尝试从文本中提取报价
            if not prices:
                # 尝试提取报价（金额或下浮率）
                price_matches = re.findall(r'(?:报价|投标价|下浮率)[：:]\s*([\d%.]+)', full_text)
                if not price_matches:
                    price_matches = re.findall(r'[\d,]+\.?\d*\s*[元%]', full_text)
                
                if price_matches:
                    prices = [match.strip() for match in price_matches]
            
            # 构建完整URL
            infourl = data.get("infourl", "")
            full_url = f"{self.base_url}{infourl}" if infourl and infourl.startswith("/") else infourl
            
            return {
                "project_name": project_name,
                "publicity_period": publicity_period,
                "bidders": bidders,
                "prices": prices,
                "full_url": full_url
            }
        except Exception as e:
            print(f"[解析错误] 解析HTML内容失败: {str(e)}")
            return {}

    def _build_message(self, record: Dict) -> str:
        """构建通知消息"""
        try:
            parsed_data = record.get("parsed_data", {})
            raw_data = record.get("raw_data", {})
            
            # 构建中标候选人表格
            markdown_table = ""
            if parsed_data.get("bidders") and parsed_data.get("prices"):
                table_header = "| 序号 | 中标候选人 | 投标报价 |\n| :----- | :----: | -------: |"
                table_rows = []
                
                # 确定最小长度，避免索引错误
                min_len = min(len(parsed_data["bidders"]), len(parsed_data["prices"]))
                
                for i in range(min_len):
                    bidder = parsed_data["bidders"][i]
                    price = parsed_data["prices"][i]
                    
                    # 格式化报价
                    try:
                        # 处理下浮率报价
                        if '%' in price:
                            formatted_price = price
                        # 处理金额报价
                        else:
                            # 移除非数字字符
                            clean_price = re.sub(r'[^\d.]', '', price)
                            if clean_price:
                                num_price = float(clean_price)
                                # 超过10万时使用万元单位
                                if num_price > 100000:
                                    formatted_price = f"{num_price/10000:,.2f}万元"
                                else:
                                    formatted_price = f"{num_price:,.2f}元"
                            else:
                                formatted_price = price
                    except:
                        formatted_price = price
                    
                    table_rows.append(f"| {i+1} | {bidder} | {formatted_price} |")
                
                markdown_table = table_header + "\n" + "\n".join(table_rows)
            
            # 构建完整消息
            message = (
                "#📢 中标候选人公告\n"
                f"📜 标题：{raw_data.get('title', '未知标题')}\n"
                f"📅 日期：{raw_data.get('infodate', '未知日期')}\n"
                f"⏳ 公示时间：{parsed_data.get('publicity_period', '')}\n\n"
            )
            
            if markdown_table:
                message += "🏆 中标候选人及报价：\n" + markdown_table + "\n\n"
            elif parsed_data.get("bidders"):
                # 没有表格时使用简单列表
                message += "🏆 中标候选人：\n"
                for i, bidder in enumerate(parsed_data["bidders"]):
                    message += f"{i+1}. {bidder}\n"
                
                # 如果有报价但不匹配数量
                if parsed_data.get("prices"):
                    message += "\n💰 投标报价：\n"
                    for price in parsed_data["prices"]:
                        message += f"- {price}\n"
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
        """发送企业微信通知markdown_v2"""
        try:
            payload = {
                "msgtype": "markdown_v2",
                "markdown_v2": {
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
