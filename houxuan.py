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
        self.base_url = "https://jyj.zhijiang.gov.cn"  # 基础URL
        
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
            
            # 提取公示时间 - 修复逻辑
            publicity_period = ""
            infocontent = data.get("infocontent", "")
            soup = BeautifulSoup(infocontent, 'html.parser')
            
            # 方法1：尝试从段落中提取
            for p in soup.find_all('p'):
                text = p.get_text().strip()
                if "公示期为" in text:
                    # 使用正则表达式精确提取时间段
                    match = re.search(r"公示期为(.+?)\n", text)
                    if match:
                        publicity_period = match.group(1).strip()
                        break
                    # 如果没有换行符，尝试分割提取
                    else:
                        parts = text.split("公示期为")
                        if len(parts) > 1:
                            publicity_period = parts[1].split("。")[0].strip()
                            break
            
            # 方法2：如果段落提取失败，尝试从表格后的文本中提取
            if not publicity_period:
                tables = soup.find_all('table')
                if tables:
                    last_table = tables[-1]
                    next_element = last_table.find_next_sibling()
                    while next_element:
                        if next_element.name == 'p' and "公示期为" in next_element.get_text():
                            text = next_element.get_text().strip()
                            match = re.search(r"公示期为(.+?)\n", text)
                            if match:
                                publicity_period = match.group(1).strip()
                                break
                        next_element = next_element.find_next_sibling()
            
            # 方法3：作为最后手段，使用简单文本搜索
            if not publicity_period:
                text_content = soup.get_text()
                match = re.search(r"公示期为(.+?)\n", text_content)
                if match:
                    publicity_period = match.group(1).strip()
                elif "公示期为" in text_content:
                    parts = text_content.split("公示期为")
                    if len(parts) > 1:
                        publicity_period = parts[1].split("。")[0].split("\n")[0].strip()
            
            # 提取中标候选人及报价
            bidders = []
            prices = []
            
            # 查找包含评标结果的表格
            for table in soup.find_all('table'):
                # 查找表头行，确定列位置
                header_found = False
                bidder_col = -1
                price_col = -1
                
                # 遍历所有行查找表头
                for i, row in enumerate(table.find_all('tr')):
                    cells = row.find_all(['th', 'td'])
                    cell_texts = [cell.get_text(strip=True) for cell in cells]
                    
                    # 查找包含关键字的表头
                    if any("中标候选人" in text or "候选人名称" in text for text in cell_texts):
                        header_found = True
                        
                        # 确定列位置
                        for col_idx, text in enumerate(cell_texts):
                            if "中标候选人" in text or "候选人名称" in text:
                                bidder_col = col_idx
                            elif "投标报价" in text or "报价" in text:
                                price_col = col_idx
                        
                        # 如果找到表头，开始提取数据行
                        data_rows = table.find_all('tr')[i+1:]  # 表头之后的所有行
                        for data_row in data_rows:
                            data_cells = data_row.find_all('td')
                            if len(data_cells) > max(bidder_col, price_col):
                                # 提取投标人
                                if bidder_col >= 0:
                                    bidder = data_cells[bidder_col].get_text(strip=True)
                                    # 过滤掉无效数据
                                    if len(bidder) > 2 and not any(keyword in bidder for keyword in 
                                                                  ["下浮率", "质量", "目标", "设计", "施工"]):
                                        bidders.append(bidder)
                                
                                # 提取报价
                                if price_col >= 0:
                                    price = data_cells[price_col].get_text(strip=True)
                                    # 过滤掉无效数据
                                    if any(char in price for char in ["元", "%", ".", "万"]) and len(price) < 100:
                                        prices.append(price)
                        
                        # 找到有效表头后跳出当前表格
                        break
                
                # 如果找到有效数据，停止搜索其他表格
                if bidders and prices:
                    break
            
            # 备用方案：如果上述方法找不到，尝试基于位置提取
            if not bidders or not prices:
                # 原始方法作为备用
                for table in soup.find_all('table'):
                    rows = table.find_all('tr')
                    if len(rows) > 1 and any("中标候选人" in row.get_text() for row in rows[:2]):
                        # 尝试找到数据最密集的行
                        candidate_rows = []
                        for row in rows[1:]:
                            cells = row.find_all('td')
                            if len(cells) > 2:  # 至少3列数据
                                # 检查单元格内容特征
                                valid_cells = sum(1 for cell in cells if len(cell.get_text(strip=True)) > 2)
                                if valid_cells >= 2:
                                    candidate_rows.append(cells)
                        
                        if candidate_rows:
                            # 取前3-5个候选行
                            for cells in candidate_rows[:5]:
                                # 投标人通常是第一个有意义的内容
                                bidder_candidate = cells[0].get_text(strip=True)
                                if len(bidder_candidate) > 2 and not any(keyword in bidder_candidate for keyword in 
                                                                      ["下浮率", "质量", "目标", "设计", "施工"]):
                                    bidders.append(bidder_candidate)
                                
                                # 报价通常是数字最多的列
                                for cell in cells[1:]:
                                    text = cell.get_text(strip=True)
                                    if any(char.isdigit() for char in text) and any(char in text for char in ["元", ".", "%"]):
                                        prices.append(text)
                                        break
                            
                            if bidders and prices:
                                break
            
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
                table_header = "| 序号 | 中标候选人 | 投标报价(元) |\n| :----- | :----: | -------: |"
                table_rows = []
                
                for i, (bidder, price) in enumerate(zip(parsed_data["bidders"], parsed_data["prices"])):
                    try:
                        # 格式化金额为千位分隔
                        formatted_price = f"{float(price.replace(',', '')):,.2f}"
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
