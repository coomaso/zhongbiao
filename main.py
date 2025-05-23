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
        self.original_file = "zb.json"
        self.parsed_file = "parsed.json"
        
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
        self.category_num = "003001005"
        self.page_size = 6
        self.latest_new_count = 0  # 跟踪最新新增数量

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

    def _parse_html_content(self, html: str) -> Dict:
        """解析HTML表格"""
        result = {}
        try:
            soup = BeautifulSoup(html, 'html.parser')
            table = soup.find("table")
            if not table:
                return result

            for row in table.find_all("tr"):
                cols = [td.get_text(strip=True) for td in row.find_all("td")]
                self._process_table_row(cols, result)

            # 备用解析方式
            if not result.get("中标人"):
                result["中标人"] = self._fallback_extract(html, r"中标(人|单位)")
                
        except Exception as e:
            print(f"[解析错误] {str(e)}")
        return result

    def _process_table_row(self, columns: List[str], result: Dict):
        """处理表格行"""
        if len(columns) >= 2:
            key = self._normalize_key(columns[0])
            result[key] = columns[1]
        if len(columns) >= 4:
            key = self._normalize_key(columns[2])
            result[key] = columns[3]

    def _normalize_key(self, text: str) -> str:
        """标准化键名"""
        return re.sub(r'[:：\s]+', '', text).strip()

    def _build_message(self, record: Dict) -> str:
        """构建消息模板"""
        parsed = record.get("parsed_data", {})
        raw = record.get("raw_data", {})
        
        # 动态字段匹配
        bidder = self._find_field(parsed, r"中标(人|单位)")
        price = self._find_field(parsed, r"中标(价|金额)")
        
        return (
            f"📢 新中标公告\n"
            f"----------------------------\n"
            f"▪ 标题：{raw.get('title', '未知标题')}\n"
            f"▪ 日期：{raw.get('infodate', '未知日期')}\n"
            f"▪ 中标方：{bidder}\n"
            f"▪ 中标金额：{price}\n"
            f"🔗 详情链接：{self._build_full_url(record.get('infourl', ''))}\n"
            f"----------------------------"
        )

    def _find_field(self, data: Dict, pattern: str) -> str:
        """正则匹配字段"""
        for key in data:
            if re.search(pattern, key):
                return data[key]
        return self._fallback_extract(data.get("raw_html", ""), pattern)

    def _fallback_extract(self, html: str, pattern: str) -> str:
        """备用解析方法"""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            td = soup.find(string=re.compile(pattern))
            return td.find_next('td').get_text(strip=True) if td else "未找到"
        except:
            return "解析失败"

    def _build_full_url(self, path: str) -> str:
        """构建完整URL"""
        if not path.startswith("/"):
            return "链接无效"
        return f"https://ggzy.sc.yichang.gov.cn{path}"

    def _send_wechat(self, message: str, webhook: str):
        """发送企业微信通知"""
        payload = {
            "msgtype": "text",
            "text": {"content": message}
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
