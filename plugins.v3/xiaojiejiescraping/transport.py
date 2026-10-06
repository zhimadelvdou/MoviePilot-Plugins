"""匿名来源的 SDK 传输配置：只继承显式 MP 代理，不隐式使用 netrc 凭据。"""
# SPDX-License-Identifier: GPL-3.0-only
from http.cookiejar import DefaultCookiePolicy

import requests
from app.sdk.network import AsyncRequestUtils, RequestUtils

from .diagnostics import diagnose


def _diagnostic_json(response, source, operation):
    """保留 SDK JSON 便捷方法的返回语义，仅从响应对象提取安全诊断。"""
    if source is not None:
        status = getattr(response, "status_code", None)
        reason = ("transport_unavailable" if response is None else
                  "http_error" if type(status) is int and 400 <= status <= 599 else
                  "http_redirect" if type(status) is int and 300 <= status <= 399 else "http_response")
        diagnose(source, operation, reason, status=status)
    # requests 与异步 SDK 的响应真值语义不同；这里不改变原先的判定。
    if response:
        try:
            payload = response.json()
            if payload is None and source is not None:
                diagnose(source, operation, "payload_invalid")
            return payload
        except Exception:
            if source is not None:
                diagnose(source, operation, "invalid_json")
    return None


class _NoCookies(DefaultCookiePolicy):
    """不在匿名元数据会话中接收或回送站点 Cookie。"""
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


class AnonymousRequestUtils(RequestUtils):
    """使用 SDK 文档支持的调用方 Session，关闭环境身份并显式回收。"""
    def __init__(self, **kwargs):
        session = requests.Session()
        session.trust_env = False
        session.cookies.set_policy(_NoCookies())
        self._anonymous_session = session
        try:
            super().__init__(session=session, **kwargs)
        except Exception:
            session.close()
            raise

    def close(self):
        """SDK 不拥有传入会话，插件负责关闭自己创建的匿名会话。"""
        super().close()
        self._anonymous_session.close()

    def get_json(self, url, params=None, *, diagnostic_source=None, diagnostic_operation=None, **kwargs):
        """使用公共 SDK request 观测状态，仍关闭响应及插件持有的会话。"""
        response = None
        try:
            response = self.request(method="get", url=url, params=params, **kwargs)
            return _diagnostic_json(response, diagnostic_source, diagnostic_operation)
        except Exception:
            if diagnostic_source is not None:
                diagnose(diagnostic_source, diagnostic_operation, "request_exception")
            raise
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                self.close()


class AnonymousAsyncRequestUtils(AsyncRequestUtils):
    """异步 SDK 同样禁止环境隐式身份；代理仍由调用方传 settings.PROXY。"""
    def __init__(self, **kwargs):
        super().__init__(trust_env=False, **kwargs)

    async def get_json(self, url, params=None, *, diagnostic_source=None, diagnostic_operation=None, **kwargs):
        """异步查询同样只记录安全状态，不输出异常文本或响应内容。"""
        response = None
        try:
            response = await self.request(method="get", url=url, params=params, **kwargs)
            return _diagnostic_json(response, diagnostic_source, diagnostic_operation)
        except Exception:
            if diagnostic_source is not None:
                diagnose(diagnostic_source, diagnostic_operation, "request_exception")
            raise
        finally:
            if response is not None:
                await response.aclose()
