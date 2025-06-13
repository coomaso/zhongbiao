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
        self.category_num = "003001004" # 中标候选人类别
        self.page_size = 6
        self.latest_new_count = 0  # 跟踪最新新增数量
        
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
                "parsed_data": self._parse_html_content(item.get("infocontent", "")),
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
                "parsed_data": self._parse_html_content(item.get("infocontent", "")),
                "raw_data": {
                    "title": item.get("title"),
                    "infodate": item.get("infodate")
                }
            }
            parsed_data.append(parsed_record)
        
        self._save_json_file(self.parsed_file, parsed_data)
        self.latest_new_count = len(new_items)  # 保存最新数量
        return self.latest_new_count

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
            
            # 中标特别通知
            if "盛荣" in record.get("parsed_data", {}).get("中标人", ""):
                if self.webhook_zb_url:
                    self._send_wechat(f"【中标通知】\n{message}", self.webhook_zb_url)

def extract_bid_info(data):
    # 提取项目名称
    project_name = data["customtitle"].replace("中标候选人公示", "").strip()
    
    # 提取公示时间
    publicity_period = ""
    infocontent = data["infocontent"]
    soup = BeautifulSoup(infocontent, 'html.parser')
    for p in soup.find_all('p'):
        if "公示期为" in p.get_text():
            publicity_period = p.get_text().split("公示期为")[1].strip()
            break
    
    # 提取中标候选人及报价
    bidders = []
    prices = []
    
    # 查找包含评标结果的表格
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) > 1 and "中标候选人名称" in rows[0].get_text():
            # 提取候选人名称行
            bidder_row = rows[1].find_all('td')
            if bidder_row:
                bidders = [td.get_text(strip=True) for td in bidder_row[1:]]
            
            # 提取报价行
            price_row = rows[2].find_all('td')
            if price_row:
                prices = [td.get_text(strip=True) for td in price_row[1:]]
            break
    
    # 构建Markdown表格
    table_header = "| 序号 | 中标候选人 | 投标报价(元) |\n| :----- | :----: | -------: |"
    table_rows = []
    
    for i, (bidder, price) in enumerate(zip(bidders, prices)):
        try:
            # 格式化金额为千位分隔
            formatted_price = f"{float(price.replace(',', '')):,.2f}"
        except:
            formatted_price = price
        table_rows.append(f"| {i+1} | {bidder} | {formatted_price} |")
    
    markdown_table = table_header + "\n" + "\n".join(table_rows)
    
    # 提取详情URL
    infourl = data["infourl"]
    base_url = "https://jyj.zhijiang.gov.cn"  # 根据实际情况可能需要调整
    full_url = f"{base_url}{infourl}" if infourl.startswith("/") else infourl
    
    # 构建完整输出
    return (
        "📢 新中标公告\n"
        f"  📜 标题：{data['title']}\n"
        f"  📅 日期：{data['infodate']}\n"
        f"  ⏳ 公示时间：{publicity_period}\n\n"
        "🏆 中标候选人及报价：\n"
        f"{markdown_table}\n\n"
        f"🔗 详情链接：{full_url}"
    )

    def _send_wechat(self, message: str, webhook: str):
        """发送企业微信通知markdown_v2"""
        payload = {
            "msgtype": "markdown_v2",
            "markdown_v2":  {"content": message}
        }
        try:
            response = requests.post(webhook, json=payload, timeout=10)
            response.raise_for_status()
            print(f"[通知成功] 发送到 {webhook}")
        except Exception as e:
            print(f"[通知失败] {str(e)}")

    # 其他辅助方法保持不变...
    
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
