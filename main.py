import json
import requests
import datetime
import os
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Any

class BidMonitor:
    def __init__(self):
        # 初始化文件路径
        self.original_file = "zb.json"  # 原始数据存储
        self.parsed_file = "parsed.json"      # 解析结果存储
        
        # 企业微信配置
        self.webhook_url = os.getenv("QYWX_URL")
        self.webhook_zb_url = os.getenv("QYWX_ZB_URL")
        
        # API配置
        self.api_url = "https://ggzy.sc.yichang.gov.cn/EpointWebBuilder/rest/secaction/getSecInfoListYzm"
        self.site_guid = "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
        self.category_num = "003001005"
        self.page_size = 6  # 每页获取数量

    def reparse_all_data(self):
        """从原始数据文件重新解析所有数据"""
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
        date_range = self._get_date_range(7)  # 获取近7天数据
        payload = {
            "siteGuid": self.site_guid,
            "categoryNum": self.category_num,
            "pageindex": "0",
            "pagesize": str(self.page_size),
            **date_range
        }
        retries = 3
        for attempt in range(retries):
            try:
                response = requests.post(self.api_url, data=payload, timeout=30)
                response.raise_for_status()
                return response.json().get("custom", {}).get("infodata", [])
            except requests.exceptions.Timeout:
                print(f"[第 {attempt+1} 次尝试] 请求超时，等待重试...")
            except requests.exceptions.ConnectionError:
                print(f"[第 {attempt+1} 次尝试] 网络连接错误，等待重试...")
            except requests.RequestException as e:
                print(f"[第 {attempt+1} 次尝试] 请求失败: {str(e)}")
            time.sleep(5)
        
        print("[最终失败] 无法获取数据，已达最大重试次数")
        return []

    def process_and_store_data(self) -> int:
        """处理数据并返回新增数量"""
        # 获取新数据
        new_raw_data = self.fetch_latest_data()
        if not new_raw_data:
            return 0

        # 加载已有原始数据
        existing_raw = self._load_json_file(self.original_file)
        
        # 过滤新数据
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
                "raw_data": {  # 保留关键原始字段
                    "title": item.get("title"),
                    "infodate": item.get("infodate")
                }
            }
            parsed_data.append(parsed_record)
        
        self._save_json_file(self.parsed_file, parsed_data)
        return len(new_items)

    def send_notifications(self):
        """发送最新通知"""
        # 获取最新解析数据
        parsed_data = self._load_json_file(self.parsed_file)
        if not parsed_data:
            return

        # 获取最近处理数量
        new_count = min(len(parsed_data), self.page_size)
        latest_parsed = parsed_data[-new_count:]
        
        for record in latest_parsed:
            # 构建消息内容
            message = self._build_message(record)
            if not message:
                continue

            # 发送常规通知
            self._send_wechat(message, self.webhook_url)
            
            # 特殊关键词检测
            if "盛荣" in record.get("parsed_data", {}).get("中标人", ""):
                self._send_wechat(message, self.webhook_zb_url)

    def _parse_html_content(self, html: str) -> Dict:
        """解析HTML内容为结构化数据"""
        result = {}
        try:
            soup = BeautifulSoup(html, 'html.parser')
            table = soup.find("table")
            if not table:
                return result

            for row in table.find_all("tr"):
                cols = [td.get_text(strip=True) for td in row.find_all("td")]
                self._process_table_row(cols, result)

        except Exception as e:
            print(f"[解析错误] HTML解析失败: {str(e)}")
        return result

    def _process_table_row(self, columns: List[str], result: Dict):
        """处理表格行数据"""
        # 处理第一组键值对
        if len(columns) >= 2:
            key = self._clean_key(columns[0])
            result[key] = columns[1]
        
        # 处理第二组键值对
        if len(columns) >= 4:
            key = self._clean_key(columns[2])
            result[key] = columns[3]

    def _clean_key(self, text: str) -> str:
        """清洗键名字符串"""
        return re.sub(r'[:：\s]', '', text).strip()
        
    def _find_field_by_regex(self, parsed: Dict[str, Any], pattern: str) -> str:
        """正则匹配关键字"""
        for key, value in parsed.items():
            if re.search(pattern, key):
                return value
        return "未找到信息"
        
    def _build_message(self, record: Dict) -> str:
        """构建通知消息"""
        parsed = record.get("parsed_data", {})
        raw = record.get("raw_data", {})
        
        # 获取关键字段
        title = raw.get("title", "未知标题")
        date = raw.get("infodate", "未知日期")
        bidder = parsed.get("中标人", self._fallback_extract(record, "中标人"))
        price = self._find_field_by_regex(parsed, r"中标价")
        url = self._build_full_url(record.get("infourl", ""))
        
        return (
            f"新公告：{title}\n"
            f"发布日期：{date}\n"
            f"中标单位：{bidder}\n"
            f"中标金额：{price}\n"
            f"详情链接：{url}"
        )

    def _fallback_extract(self, record: Dict, field: str) -> str:
        """备选字段提取方法"""
        html = self._find_raw_html(record.get("infoid"))
        if not html:
            return "未找到信息"
        
        soup = BeautifulSoup(html, 'html.parser')
        td = soup.find('td', string=re.compile(fr'{field}[:：]'))
        return td.find_next_sibling('td').get_text(strip=True) if td else "未找到信息"

    def _find_raw_html(self, infoid: str) -> str:
        """通过ID查找原始HTML"""
        raw_data = self._load_json_file(self.original_file)
        for item in raw_data:
            if item.get("infoid") == infoid:
                return item.get("infocontent", "")
        return ""

    def _build_full_url(self, path: str) -> str:
        """构建完整URL"""
        return f"https://ggzy.sc.yichang.gov.cn{path}" if path else ""

    def _send_wechat(self, message: str, webhook: str):
        """发送企业微信通知"""
        if not webhook:
            print("[通知错误] Webhook地址未配置")
            return

        payload = {
            "msgtype": "text",
            "text": {"content": message}
        }
        
        try:
            response = requests.post(webhook, json=payload, timeout=10)
            response.raise_for_status()
            print(f"[通知成功] 消息已发送至 {webhook}")
        except Exception as e:
            print(f"[通知失败] {str(e)}")

    def _get_date_range(self, days: int) -> Dict:
        """生成时间范围"""
        today = datetime.datetime.now()
        return {
            "startdate": (today - datetime.timedelta(days=days)).strftime("%Y-%m-%d 00:00:00"),
            "enddate": today.strftime("%Y-%m-%d 23:59:59")
        }

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
        """检查记录是否存在"""
        new_id = new_item.get("infoid")
        new_url = new_item.get("infourl")
        return any(
            item.get("infoid") == new_id or 
            item.get("infourl") == new_url
            for item in existing
        )

if __name__ == "__main__":
    import sys

    monitor = BidMonitor()

    # 命令行参数支持
    if "--reparse-all" in sys.argv:
        monitor.reparse_all_data()
    else:
        # 正常监控流程
        new_count = monitor.process_and_store_data()
        if new_count > 0:
            print(f"发现 {new_count} 条新数据，开始发送通知...")
            monitor.send_notifications()
        else:
            print("没有检测到新数据")
