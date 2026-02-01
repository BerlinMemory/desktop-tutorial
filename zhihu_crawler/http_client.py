"""
HTTP 客户端模块
提供限速、重试、UA轮换等功能
"""
import time
import random
from typing import Dict, Optional
import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)


class RateLimiter:
    """简单的令牌桶限速器"""

    def __init__(self, requests_per_second: float = 1.0):
        """
        初始化限速器
        :param requests_per_second: 每秒允许的请求数
        """
        self.interval = 1.0 / requests_per_second
        self.last_request_time = 0
        self.adaptive_delay = 0  # 自适应延迟（遇到限流时增加）

    def wait(self):
        """等待到下一个请求时刻"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        total_wait = self.interval + self.adaptive_delay

        if time_since_last < total_wait:
            sleep_time = total_wait - time_since_last
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def increase_delay(self):
        """遇到限流时增加延迟"""
        self.adaptive_delay = min(self.adaptive_delay + 1, 10)
        print(f"触发限流，增加延迟至 {self.adaptive_delay:.1f}s")

    def reset_delay(self):
        """重置自适应延迟"""
        if self.adaptive_delay > 0:
            print(f"恢复正常速度")
            self.adaptive_delay = 0


class UserAgentPool:
    """User-Agent 池"""

    def __init__(self):
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]

    def get_random(self) -> str:
        """随机获取一个 User-Agent"""
        return random.choice(self.user_agents)


class ZhihuHTTPClient:
    """知乎 HTTP 客户端"""

    def __init__(self, cookie: str, requests_per_second: float = 1.5,
                 retry_times: int = 3, retry_backoff: int = 2):
        """
        初始化客户端
        :param cookie: 知乎登录 Cookie
        :param requests_per_second: 每秒请求数
        :param retry_times: 最大重试次数
        :param retry_backoff: 重试退避倍数
        """
        self.cookie = cookie
        self.retry_times = retry_times
        self.retry_backoff = retry_backoff
        self.rate_limiter = RateLimiter(requests_per_second)
        self.ua_pool = UserAgentPool()
        self.session = requests.Session()

        # 禁用代理（避免代理连接问题）
        self.session.trust_env = False

        # 设置默认超时
        self.timeout = 30

    def _get_headers(self) -> Dict[str, str]:
        """生成请求头"""
        return {
            'User-Agent': self.ua_pool.get_random(),
            'Cookie': self.cookie,
            'Referer': 'https://www.zhihu.com/',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }

    def _handle_rate_limit(self, response: requests.Response) -> bool:
        """
        处理限流响应
        :return: 是否触发了限流
        """
        if response.status_code in [429, 403]:
            self.rate_limiter.increase_delay()
            return True
        else:
            self.rate_limiter.reset_delay()
            return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        retry=retry_if_exception_type((requests.RequestException, requests.Timeout))
    )
    def get(self, url: str, params: Optional[Dict] = None,
            retry_on_rate_limit: bool = True) -> Optional[Dict]:
        """
        发送 GET 请求
        :param url: 请求 URL
        :param params: 查询参数
        :param retry_on_rate_limit: 是否在限流时重试
        :return: JSON 响应数据，失败返回 None
        """
        # 限速等待
        self.rate_limiter.wait()

        try:
            response = self.session.get(
                url,
                params=params,
                headers=self._get_headers(),
                timeout=self.timeout
            )

            # 检查限流
            if self._handle_rate_limit(response):
                if retry_on_rate_limit:
                    print(f"触发限流，等待后重试: {url}")
                    time.sleep(self.rate_limiter.adaptive_delay)
                    return self.get(url, params, retry_on_rate_limit=False)
                else:
                    print(f"请求失败(限流): {url}")
                    return None

            # 检查响应状态
            if response.status_code == 200:
                return response.json()
            else:
                print(f"请求失败(状态码 {response.status_code}): {url}")
                return None

        except requests.Timeout:
            print(f"请求超时: {url}")
            raise  # 让 tenacity 处理重试
        except requests.RequestException as e:
            print(f"请求异常: {url}, 错误: {e}")
            raise  # 让 tenacity 处理重试
        except Exception as e:
            print(f"未知错误: {url}, 错误: {e}")
            return None

    def close(self):
        """关闭会话"""
        self.session.close()


# ==================== 知乎 API 封装 ====================

class ZhihuAPI:
    """知乎 API 接口封装"""

    def __init__(self, client: ZhihuHTTPClient):
        self.client = client

    def search_questions(self, keyword: str, offset: int = 0,
                        limit: int = 20) -> Optional[Dict]:
        """
        搜索问题
        :param keyword: 搜索关键词
        :param offset: 偏移量
        :param limit: 返回数量
        :return: API 响应数据
        """
        url = "https://www.zhihu.com/api/v4/search_v3"
        params = {
            't': 'general',
            'q': keyword,
            'offset': offset,
            'limit': limit
        }
        return self.client.get(url, params)

    def get_question_answers(self, question_id: str, offset: int = 0,
                            limit: int = 20, sort_by: str = 'default') -> Optional[Dict]:
        """
        获取问题的回答列表
        :param question_id: 问题 ID
        :param offset: 偏移量
        :param limit: 返回数量
        :param sort_by: 排序方式 (default/updated)
        :return: API 响应数据
        """
        url = f"https://www.zhihu.com/api/v4/questions/{question_id}/answers"
        params = {
            'offset': offset,
            'limit': limit,
            'sort_by': sort_by
        }
        return self.client.get(url, params)

    def get_answer_root_comments(self, answer_id: str, offset: int = 0,
                                 limit: int = 20, order: str = 'normal') -> Optional[Dict]:
        """
        获取回答的主评论（根评论）
        :param answer_id: 回答 ID
        :param offset: 偏移量
        :param limit: 返回数量
        :param order: 排序方式 (normal/score)
        :return: API 响应数据
        """
        url = f"https://www.zhihu.com/api/v4/answers/{answer_id}/root_comments"
        params = {
            'order': order,
            'offset': offset,
            'limit': limit
        }
        return self.client.get(url, params)

    def get_comment_child_comments(self, comment_id: str, offset: int = 0,
                                   limit: int = 20) -> Optional[Dict]:
        """
        获取评论的子评论（楼中楼）
        :param comment_id: 评论 ID
        :param offset: 偏移量
        :param limit: 返回数量
        :return: API 响应数据
        """
        url = f"https://www.zhihu.com/api/v4/comments/{comment_id}/child_comments"
        params = {
            'offset': offset,
            'limit': limit
        }
        return self.client.get(url, params)


if __name__ == "__main__":
    # 测试代码
    print("HTTP 客户端模块已加载")
    print("请在配置文件中设置 Cookie 后使用")
