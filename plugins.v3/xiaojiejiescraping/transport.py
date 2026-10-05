"""匿名来源的 SDK 传输配置：只继承显式 MP 代理，不隐式使用 netrc 凭据。"""
# SPDX-License-Identifier: GPL-3.0-only
from http.cookiejar import DefaultCookiePolicy

import requests
from app.sdk.network import AsyncRequestUtils, RequestUtils


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

    def get_json(self, url, params=None, **kwargs):
        """JSON 便捷调用也必须关闭调用方会话，而非只关闭 HTTP 响应。"""
        try:
            return super().get_json(url, params=params, **kwargs)
        finally:
            self.close()


class AnonymousAsyncRequestUtils(AsyncRequestUtils):
    """异步 SDK 同样禁止环境隐式身份；代理仍由调用方传 settings.PROXY。"""
    def __init__(self, **kwargs):
        super().__init__(trust_env=False, **kwargs)
