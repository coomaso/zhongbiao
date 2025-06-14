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
        """解析HTML内容，提取项目信息和中标候选人列表"""
        try:
            project_name = data.get("customtitle", "").replace("中标候选人公示", "").strip()
            infocontent = data.get("infocontent", "")
            soup = BeautifulSoup(infocontent, 'html.parser')
            full_text = soup.get_text()
    
            # 提取公示时间
            publicity_period = ""
            pub_patterns = [
                r"公示[期时]为?[:：]?\s*(.+?至.+?)\s*(?:\n|<|$)",
                r"公示时间[:：]?\s*(.+?至.+?)\s*(?:\n|<|$)",
                r"公示期[:：]?\s*(.+?至.+?)\s*(?:\n|<|$)"
            ]
            for pattern in pub_patterns:
                match = re.search(pattern, full_text)
                if match:
                    publicity_period = match.group(1).strip()
                    break
    
            bidders_and_prices = []
            seen_bidders = set()
    
            # 表格提取（增强结构识别）
            for table in soup.find_all('table'):
                header_text = table.get_text()
                if not any(key in header_text for key in ["中标候选人", "投标人", "单位名称", "报价", "下浮率"]):
                    continue  # 不含关键词，跳过
    
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) < 2:
                        continue
    
                    cell_texts = [cell.get_text(strip=True) for cell in cells]
                    row_text = ''.join(cell_texts)
    
                    # 寻找包含公司名的行
                    if any("公司" in c or "集团" in c for c in cell_texts):
                        for cell in cell_texts:
                            bidder = cell.strip()
                            if bidder and bidder not in seen_bidders and any(kw in bidder for kw in ["公司", "集团", "设计院", "工程"]):
                                seen_bidders.add(bidder)
                                bidders_and_prices.append({"bidder": bidder, "price": "未提供"})
    
                    # 如果包含“报价”关键词，尝试提取价格
                    elif any("报价" in c or "下浮率" in c or "%" in c for c in cell_texts):
                        prices = []
                        for c in cell_texts:
                            c = c.replace('\xa0', '').strip()
                            if re.search(r"([\d,.]+(万元|元|%))", c) or "下浮率" in c:
                                prices.append(c)
                        # 把价格匹配到已有的 bidder 上（按顺序）
                        for i in range(min(len(prices), len(bidders_and_prices))):
                            if bidders_and_prices[i]["price"] == "未提供":
                                bidders_and_prices[i]["price"] = prices[i]
            
            # 如果表格未识别，尝试文本提取作为备选
            if not bidders_and_prices:
                company_pattern = r'([\u4e00-\u9fa5]{2,}(公司|集团|设计院|研究院|工程局|有限公司|股份公司))'
                price_pattern = r'(?:报价|金额|下浮率|投标报价)[：:\s]*([\d,.%万元元]+)'
    
                companies = re.findall(company_pattern, full_text)
                unique_companies = []
                seen = set()
                for c, _ in companies:
                    if c not in seen:
                        seen.add(c)
                        unique_companies.append(c)
    
                prices = re.findall(price_pattern, full_text)
                for i, company in enumerate(unique_companies[:5]):
                    price = prices[i] if i < len(prices) else "未提供"
                    bidders_and_prices.append({
                        "bidder": company,
                        "price": price
                    })
    
            # 构建最终数据结构
            infourl = data.get("infourl", "")
            full_url = f"{self.base_url}{infourl}" if infourl.startswith("/") else infourl
    
            return {
                "project_name": project_name or "未知项目",
                "publicity_period": publicity_period,
                "bidders_and_prices": bidders_and_prices,
                "full_url": full_url
            }
    
        except Exception as e:
            print(f"[解析错误] {str(e)}")
            return {
                "project_name": project_name or "解析失败",
                "publicity_period": "",
                "bidders_and_prices": [{"bidder": "解析失败", "price": "请查看详情"}],
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
                table_header = "|中标候选人|投标报价|\n| :----: | -------: |"
                table_rows = []
                
                for i, item in enumerate(bap):
                    bidder = item.get("bidder", "未提供")
                    price = item.get("price", "未提供")
                    
                    # 格式化报价
                    formatted_price = price
                    if '%' in price:
                        # If it contains a percentage, keep it as is
                        formatted_price = price
                    elif any(char.isdigit() for char in price):
                        # Remove commas and attempt to clean numerical parts
                        clean_price_str = price.replace(',', '')
                        
                        # Try to extract the number before units (元, 万元) or percentage
                        # This regex attempts to find a number at the beginning or within the string,
                        # and then capture potential units or percentages at the end.
                        match_yuan = re.search(r'([\d.]+)\s*元', clean_price_str)
                        match_wanyuan = re.search(r'([\d.]+)\s*万元', clean_price_str)
                        match_percent = re.search(r'([\d.]+)\s*%', clean_price_str)
                        
                        num_val = None
                        original_unit = None
                    
                        if match_wanyuan:
                            num_val = float(match_wanyuan.group(1))
                            original_unit = "万元"
                        elif match_yuan:
                            num_val = float(match_yuan.group(1))
                            original_unit = "元"
                        elif match_percent:
                            formatted_price = price # Already handled by the top-level '%' check, but good for robustness
                        else:
                            # If no explicit unit, try to parse it directly as a number
                            try:
                                num_val = float(re.sub(r'[^\d.]', '', clean_price_str))
                                # Heuristic: if a raw number is very large, assume it's in yuan by default
                                if num_val > 100000: # Adjust threshold as needed, >10万 seems like a good cutoff for yuan to万元 conversion
                                    original_unit = "元" # Treat as raw yuan if large
                                else:
                                    original_unit = "unknown" # Small numbers, keep as is or assume yuan
                            except ValueError:
                                pass # Not a straightforward number, keep original price
                    
                        if num_val is not None:
                            if original_unit == "元":
                                if num_val >= 10000: # Convert large yuan amounts to万元
                                    formatted_price = f"{num_val / 10000:,.2f}万元"
                                else:
                                    formatted_price = f"{num_val:,.2f}元"
                            elif original_unit == "万元":
                                formatted_price = f"{num_val:,.2f}万元"
                            elif original_unit == "unknown":
                                formatted_price = f"{num_val:,.2f}元" # Default to yuan for smaller numbers without explicit unit
                    
                    table_rows.append(f"|{bidder}|{formatted_price}|")
                
                markdown_table = table_header + "\n" + "\n".join(table_rows)
            
            # 构建完整消息
            message = (
                "## 📢 中标候选人公告\n\n"
                f">**📜 标题**：{raw_data.get('title', '未知标题')}\n\n"
                f">**📅 日期**：{raw_data.get('infodate', '未知日期')}\n\n"
                f">**⏳ 公示时间**：{parsed_data.get('publicity_period', '未提供')}\n\n"
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
            
            message += f"🔗 **详情链接**：{parsed_data.get('full_url', '')}"
            
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
