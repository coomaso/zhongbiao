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
            
            # 提取中标候选人及报价
            bidders_and_prices = []
            
            # Improved table parsing logic
            for table in soup.find_all('table'):
                header_row = None
                header_cells_text = []

                # Find the header row that contains relevant keywords
                for row_idx, row in enumerate(table.find_all('tr')):
                    current_row_cells = [td.get_text(strip=True) for td in row.find_all(['th', 'td'])]
                    
                    if any(keyword in cell for cell in current_row_cells for keyword in ["中标候选人", "投标人", "单位名称", "报价", "投标报价", "下浮率"]):
                        header_row = row
                        header_cells_text = current_row_cells
                        break
                
                if header_row:
                    bidder_col = -1
                    price_col = -1
                    
                    # Identify column indices for bidder and price
                    for i, cell_text in enumerate(header_cells_text):
                        if "候选人" in cell_text or "投标人" in cell_text or "单位名称" in cell_text:
                            bidder_col = i
                        if "报价" in cell_text or "金额" in cell_text or "下浮率" in cell_text:
                            price_col = i
                    
                    # If we found at least one of the key columns, process data rows
                    if bidder_col != -1 or price_col != -1:
                        data_rows = header_row.find_next_siblings('tr')
                        for row in data_rows:
                            cells = row.find_all(['td'])
                            
                            bidder_name = ""
                            bid_price = ""

                            if bidder_col != -1 and bidder_col < len(cells):
                                bidder_name = cells[bidder_col].get_text(strip=True)
                            
                            if price_col != -1 and price_col < len(cells):
                                bid_price = cells[price_col].get_text(strip=True)
                            
                            # Add to list only if at least one piece of information is found
                            if bidder_name or bid_price:
                                # Apply basic filtering for irrelevant table header repeats in data rows
                                if "投标人" not in bidder_name and "报价" not in bid_price and "名次" not in bidder_name:
                                    bidders_and_prices.append({
                                        "bidder": bidder_name,
                                        "price": bid_price
                                    })
            
            # Fallback for bidders and prices if table parsing yields nothing
            if not bidders_and_prices:
                # Attempt to extract candidate names from text if no table data was found
                # Prioritize explicit "第X名" format
                candidate_matches = re.findall(r'第[一二三四五]名[：:]\s*([^\n（]+)', full_text)
                if candidate_matches:
                    for match in candidate_matches:
                        bidders_and_prices.append({"bidder": match.strip(), "price": "未提供"})
                
                # If still no bidders, try to find company names
                if not bidders_and_prices:
                    company_matches = re.findall(r'([\u4e00-\u9fa5]{5,}公司|[\u4e00-\u9fa5]{5,}有限公司|[\u4e00-\u9fa5]{5,}集团)', full_text)
                    if company_matches:
                        unique_companies = list(dict.fromkeys(company_matches)) # Deduplicate while preserving order
                        for company in unique_companies:
                            bidders_and_prices.append({"bidder": company, "price": "未提供"})

                # Attempt to extract prices from text if no table data was found
                # This might result in prices without associated bidders, so it's a last resort.
                if not any(item.get("price") != "未提供" for item in bidders_and_prices):
                    price_matches = re.findall(r'(?:投标报价|报价|下浮率)[:：\s]*([\d.,%元万]+)|([\d.,]+)\s*(?:元|万元|%)', full_text)
                    for match_tuple in price_matches:
                        price_str = next((s for s in match_tuple if s), None) # Get the non-empty match
                        if price_str and len(bidders_and_prices) > len([p for p in bidders_and_prices if p["price"] != "未提供"]):
                            # Attempt to assign prices to existing bidders or add as general prices
                            for i, item in enumerate(bidders_and_prices):
                                if item.get("price") == "未提供":
                                    bidders_and_prices[i]["price"] = price_str.strip()
                                    break
                            else: # If no "未提供" prices to fill, just append
                                bidders_and_prices.append({"bidder": "未知中标人", "price": price_str.strip()})


            # Build full URL
            infourl = data.get("infourl", "")
            full_url = f"{self.base_url}{infourl}" if infourl and infourl.startswith("/") else infourl
            
            return {
                "project_name": project_name,
                "publicity_period": publicity_period,
                "bidders_and_prices": bidders_and_prices,
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
            bap = parsed_data.get("bidders_and_prices", [])
            
            if bap:
                table_header = "| 序号 | 中标候选人 | 投标报价 |\n| :----- | :----: | -------: |"
                table_rows = []
                
                for i, item in enumerate(bap):
                    bidder = item.get("bidder", "")
                    price = item.get("price", "")
                    
                    # Format price
                    formatted_price = price
                    if '%' in price:
                        formatted_price = price
                    elif any(char.isdigit() for char in price): # Only try to format if it contains digits
                        clean_price_str = re.sub(r'[^\d.]', '', price.replace(',', ''))
                        if clean_price_str:
                            try:
                                num_price = float(clean_price_str)
                                if "万元" in price:
                                    formatted_price = f"{num_price:,.2f}万元"
                                elif "元" in price or num_price > 100000: # Heuristic for large numbers assumed to be in yuan, converted to萬元
                                    formatted_price = f"{num_price/10000:,.2f}万元"
                                else: # Assume it's in yuan if no unit or small number
                                    formatted_price = f"{num_price:,.2f}元"
                            except ValueError:
                                formatted_price = price # Fallback if conversion fails
                    
                    table_rows.append(f"| {i+1} | {bidder} | {formatted_price} |")
                
                markdown_table = table_header + "\n" + "\n".join(table_rows)
            
            # Build full message
            message = (
                "**📢 中标候选人公告**\n"
                f"📜 标题：{raw_data.get('title', '未知标题')}\n"
                f"📅 日期：{raw_data.get('infodate', '未知日期')}\n"
                f"⏳ 公示时间：{parsed_data.get('publicity_period', '未提供')}\n\n"
            )
            
            if markdown_table:
                message += "**🏆 中标候选人及报价：**\n" + markdown_table + "\n\n"
            elif bap: # If no table, but some bidders/prices were extracted
                message += "**🏆 中标候选人：**\n"
                for item in bap:
                    bidder = item.get("bidder", "")
                    price = item.get("price", "")
                    if bidder and price and price != "未提供":
                        message += f"- {bidder} (报价: {price})\n"
                    elif bidder:
                        message += f"- {bidder}\n"
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
        # Ensure we only get the *new* data added in the current run
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
                "msgtype": "markdown",  # Changed to markdown for simplicity if markdown_v2 has specific requirements not met
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
