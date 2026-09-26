"""闲鱼商品发布（PC 网页端）MTOP 接口客户端。

移植自 ``XianYuApis`` 项目的 ``goofish_apis.py``（``XianyuApis.public`` 及其两个
辅助接口 ``get_public_channel`` / ``get_default_location``），改写为项目统一的
异步风格，并复用 ``utils.xianyu_utils.generate_sign`` 的 H5 端 MD5 签名方案。

与 :mod:`utils.xianyu_seller_api` 的区别：

- 卖家端接口校验来源为 ``seller.goofish.com``，否则返回 ``FAIL_SYS_SESSION_EXPIRED``；
- 发布接口是 **PC 网页端**（``www.goofish.com``）接口，origin / referer 必须指向
  ``www.goofish.com``，因此单独成模块而非并入卖家端客户端。

本模块提供的三个接口：

- :meth:`get_publish_channel` —— 发布时获取分类 / 标签推荐（``kgraph.property.recommend``）
- :meth:`get_default_location` —— 发布时获取默认发货地址（``idle.local.poi.get``）
- :meth:`publish` —— 发布商品（``mtop.idle.pc.idleitem.publish``）

``publish`` 已预留 ``item_id`` 参数以支持「重新发布」（编辑模式），完整逻辑见
各方法 docstring，重新发布的细节由调用方后续单独实现。
"""

import asyncio
import json
import time
from types import TracebackType
from typing import Any

import aiohttp
from loguru import logger
from PIL import Image

from utils import risk_control
from utils.xianyu_utils import generate_sign, trans_cookies
from utils.image_uploader import ImageUploader


class PublishApiError(Exception):
    """发布接口返回业务失败。"""

    def __init__(self, api: str, ret: list[str]):
        self.api = api
        self.ret = ret
        super().__init__(f"{api} 调用失败: {'; '.join(ret) if ret else '未知错误'}")


class XianyuItemPublishAPI:
    """闲鱼商品发布（PC 网页端）接口客户端。

    每个闲鱼账号一个实例，Cookie 由外部传入并可通过 :meth:`update_cookies` 刷新。

    注意：请求成功后 :meth:`_merge_response_cookies` 会就地改写 ``cookies_str``，
    因此单个实例不要并发调用；多账号并发请各自持有一个实例。

    用法::

        api = XianyuItemPublishAPI(cookie_id="acc1", cookies_str=cookie)
        async with api:
            res = await api.publish(
                images_path=["/path/a.jpg"],
                goods_desc="九成新相机",
                price={"current_price": 199, "original_price": 299},
                ds={"choice": "包邮", "can_self_pickup": False},
            )
    """

    APP_KEY = "34839810"
    BASE_URL = "https://h5api.m.goofish.com/h5/{api}/{version}/"
    ORIGIN = "https://www.goofish.com"
    SPM_CNT = "a21ybx.publish.0.0"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        cookie_id: str,
        cookies_str: str,
        session: aiohttp.ClientSession | None = None,
        image_uploader: ImageUploader | None = None,
    ):
        self.cookie_id = cookie_id
        self.cookies_str = cookies_str or ""
        self._session = session
        self._owns_session = session is None
        self._image_uploader = image_uploader

    async def __aenter__(self) -> "XianyuItemPublishAPI":
        await self._ensure_session()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """仅关闭自己创建的 session；外部传入的交由调用方管理。"""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def update_cookies(self, cookies_str: str) -> None:
        self.cookies_str = cookies_str or ""

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------

    def _token(self) -> str:
        """从 Cookie 取签名令牌，_m_h5_tk 的下划线前半段。"""
        if not self.cookies_str:
            return ""
        try:
            token_value = trans_cookies(self.cookies_str).get("_m_h5_tk", "")
        except ValueError:
            return ""
        return token_value.split("_")[0] if token_value else ""

    def _headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded",
            # 发布接口是 PC 网页端接口，来源必须指向 www.goofish.com
            "origin": self.ORIGIN,
            "referer": f"{self.ORIGIN}/",
            "user-agent": self.USER_AGENT,
            "cookie": self.cookies_str.replace("\n", "").replace("\r", ""),
        }

    async def _call(
        self,
        api: str,
        payload: dict[str, Any],
        *,
        version: str = "1.0",
        spm_pre: str = "",
        log_id: str = "",
        value_type: str | None = None,
        timeout: int = 20,
    ) -> dict[str, Any]:
        """调用发布接口并返回完整响应；业务失败抛 :class:`PublishApiError`。

        令牌过期时服务端会在响应里下发新的 ``_m_h5_tk``，用新令牌重签一次即可成功，
        因此对这类错误自动重试。来源校验失败（origin 不对）服务端会返回
        ``FAIL_SYS_SESSION_EXPIRED``，本模块已固定 origin 为 www.goofish.com。
        """
        if not self.cookies_str:
            raise PublishApiError(api, ["Cookie 为空，无法调用发布接口"])

        last_ret: list[str] = []
        # 熔断：账号处于风控冷却期时直接放弃，继续请求只会延长风控
        guard = risk_control.registry.get(self.cookie_id) if self.cookie_id else None
        if guard is not None and guard.is_blocked:
            raise risk_control.RiskControlBlocked(
                self.cookie_id, guard.remaining_seconds, guard.last_hit_reason
            )

        for attempt in range(2):
            data_val = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            # 毫秒时间戳：先乘再取整，避免丢失毫秒精度
            timestamp = str(int(time.time() * 1000))
            params = {
                "jsv": "2.7.2",
                "appKey": self.APP_KEY,
                "t": timestamp,
                "sign": generate_sign(timestamp, self._token(), data_val),
                "v": version,
                "type": "originaljson",
                "accountSite": "xianyu",
                "dataType": "json",
                "timeout": "20000",
                "api": api,
                "sessionOption": "AutoLoginOnly",
                "spm_cnt": self.SPM_CNT,
            }
            if spm_pre:
                params["spm_pre"] = spm_pre
            if log_id:
                params["log_id"] = log_id
            if value_type:
                params["valueType"] = value_type

            session = await self._ensure_session()
            url = self.BASE_URL.format(api=api, version=version)
            if guard is not None:
                await guard.acquire()
            try:
                async with session.post(
                    url,
                    params=params,
                    data={"data": data_val},
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    # 登录态失效时 MTOP 会返回 HTML 或跳转，不先判状态码的话
                    # 后面 response.json() 只会抛一个看不懂的解码异常
                    if response.status != 200:
                        raise PublishApiError(api, [f"HTTP {response.status}"])
                    try:
                        result = await response.json(content_type=None)
                    except (ValueError, aiohttp.ContentTypeError) as exc:
                        raise PublishApiError(api, [f"响应不是合法 JSON: {exc}"]) from exc
                    # 必须在判断结果前合并，令牌就在这次响应里
                    self._merge_response_cookies(response)
            except PublishApiError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                # 网络层异常统一转成业务异常，调用方才能只 catch 一种
                raise PublishApiError(api, [f"网络请求失败: {exc}"]) from exc

            ret_list = [str(value) for value in (result.get("ret", []) if isinstance(result, dict) else [])]
            if any("SUCCESS" in value for value in ret_list):
                if guard is not None:
                    guard.reset()
                return result

            last_ret = ret_list

            # 平台风控：立刻熔断，不重试
            if risk_control.is_risk_control_error("; ".join(ret_list)):
                if guard is not None:
                    guard.trip("; ".join(ret_list))
                raise PublishApiError(api, ret_list)

            # 服务端对令牌过期有两种拼写，且历史版本存在拼写错误（EXOIRED）
            token_expired = any(
                "TOKEN_EXPIRED" in value
                or "TOKEN_EXOIRED" in value
                or "FAIL_SYS_TOKEN_EMPTY" in value
                for value in ret_list
            )
            if not token_expired or attempt == 1:
                break
            logger.debug(f"【{self.cookie_id}】{api} 令牌过期，使用新令牌重试")

        raise PublishApiError(api, last_ret)

    def _merge_response_cookies(self, response) -> None:
        """合并响应下发的新 Cookie，保持 _m_h5_tk 等令牌为最新值。"""
        if "set-cookie" not in response.headers:
            return

        updates: dict[str, str] = {}
        for raw in response.headers.getall("set-cookie", []):
            pair = raw.split(";", 1)[0].strip()
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            if key in ("_m_h5_tk", "_m_h5_tk_enc", "cookie2", "sgcookie", "_tb_token_"):
                updates[key] = value

        if not updates:
            return

        try:
            current = trans_cookies(self.cookies_str) if self.cookies_str else {}
        except ValueError:
            current = {}
        current.update(updates)
        self.cookies_str = "; ".join(f"{k}={v}" for k, v in current.items())
        logger.debug(f"【{self.cookie_id}】发布接口 Cookie 已更新: {', '.join(updates)}")

    # ------------------------------------------------------------------
    # 发布辅助接口
    # ------------------------------------------------------------------

    async def get_publish_channel(
        self, title: str, images_info: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """发布时获取分类 / 标签推荐（移植自 ``get_public_channel``）。

        Args:
            title: 商品标题（同时作为 description 用于预测分类）。
            images_info: 已上传图片的信息列表，每项形如
                ``{"url": str, "width": int, "height": int}``。

        Returns:
            接口完整响应，发布时需要的 ``data.cardList``（标签）与
            ``data.categoryPredictResult``（预测分类）都在其中。
        """
        data: dict[str, Any] = {
            "title": title,
            "lockCpv": False,
            "multiSKU": False,
            "publishScene": "mainPublish",
            "scene": "newPublishChoice",
            "description": title,
            "imageInfos": [],
            # 客户端生成的唯一码，每次发布应不同；用毫秒时间戳保证唯一
            "uniqueCode": str(int(time.time() * 1000)),
        }
        for image_info in images_info:
            data["imageInfos"].append({
                "extraInfo": {
                    "isH": "false",
                    "isT": "false",
                    "raw": "false",
                },
                "isQrCode": False,
                "url": image_info["url"],
                "heightSize": image_info["height"],
                "widthSize": image_info["width"],
                "major": True,
                "type": 0,
                "status": "done",
            })

        return await self._call(
            "mtop.taobao.idle.kgraph.property.recommend",
            data,
            version="2.0",
            spm_pre="a21ybx.item.sidebar.1.67321598K9Vgx8",
            log_id="67321598K9Vgx8",
            value_type=None,
        )

    async def get_default_location(
        self,
        longitude: float = 118.78248347393424,
        latitude: float = 31.91629189813543,
    ) -> dict[str, Any]:
        """发布时获取默认发货地址（移植自 ``get_default_channel`` 的地址部分）。

        Args:
            longitude: 经度，用于查询就近的 POI。默认值是抓包留下的南京坐标，
                建议按账号实际所在地传入，否则所有账号都拿到同一城市的地址。
            latitude: 纬度，同上。

        Returns:
            接口完整响应，发布所需的地址在 ``data.commonAddresses[0]``。
        """
        data = {
            "longitude": longitude,
            "latitude": latitude,
        }
        return await self._call(
            "mtop.taobao.idle.local.poi.get",
            data,
            version="1.0",
            spm_pre="a21ybx.item.sidebar.1.38262218ame5nr",
            log_id="38262218ame5nr",
            value_type=None,
        )

    # ------------------------------------------------------------------
    # 发布商品
    # ------------------------------------------------------------------

    async def _upload_images(
        self,
        images_path: list[str] | None,
        image_urls: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """统一把图片整理成发布所需的 ``{"url", "width", "height"}`` 列表。

        - ``images_path``：本地文件路径，先读尺寸再上传到闲鱼 CDN 取 URL。
        - ``image_urls``：已完成上传的 CDN 图片，每项 ``{"url", "width", "height"}``，
          重新发布时复用原图可只传这个。

        ``images_path`` 与 ``image_urls`` 至少提供其一。
        """
        images_info: list[dict[str, Any]] = []

        if image_urls:
            for item in image_urls:
                images_info.append({
                    "url": item["url"],
                    "width": int(item.get("width", 0)),
                    "height": int(item.get("height", 0)),
                })

        if images_path:
            uploader = self._image_uploader or ImageUploader(self.cookies_str)
            created_session = False
            try:
                if getattr(uploader, "session", None) is None:
                    await uploader.create_session()
                    created_session = True
                for path in images_path:
                    try:
                        with Image.open(path) as img:
                            width, height = img.size
                    except Exception as e:
                        logger.warning(f"【{self.cookie_id}】读取图片尺寸失败 {path}: {e}")
                        width, height = 0, 0
                    url = await uploader.upload_image(path)
                    if not url:
                        logger.error(f"【{self.cookie_id}】图片上传失败，跳过: {path}")
                        continue
                    images_info.append({"url": url, "width": width, "height": height})
            finally:
                # 谁创建的 session 谁关，外部传进来的保持原样交还调用方
                if created_session:
                    await uploader.close_session()

        if not images_info:
            raise PublishApiError(
                "mtop.idle.pc.idleitem.publish", ["没有提供任何有效图片"]
            )
        return images_info

    async def publish(
        self,
        *,
        goods_desc: str,
        ds: dict[str, Any],
        images_path: list[str] | None = None,
        image_urls: list[dict[str, Any]] | None = None,
        price: dict[str, Any] | None = None,
        item_id: str | None = None,
        category: dict[str, str] | None = None,
        address: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """发布（或重新发布）商品，移植自 ``XianyuApis.public``。

        Args:
            goods_desc: 商品标题 / 描述（当前实现标题与描述相同）。
            ds: 运费设置，字段见 ``DeliverySettings``：
                ``choice`` 取 ``包邮`` / ``按距离计费`` / ``一口价`` / ``无需邮寄``；
                ``post_price``（一口价时的运费，元）；``can_self_pickup``（是否支持自提）。
            images_path: 本地图片路径列表，会先上传到闲鱼 CDN。
            image_urls: 已上传的 CDN 图片列表，每项 ``{"url", "width", "height"}``；
                重新发布复用原图时只用这个即可。
            price: 价格，``{"current_price": 199, "original_price": 299}``，
                值为 int 或 float；不传则使用默认价。
            item_id: 传入即进入「编辑 / 重新发布」模式，把该字段带给发布网关。
                重新发布已下架商品时，建议同时传入该商品原有的 ``category`` / ``address``
                （见下），否则会退回用分类预测 + 默认地址（可能不准确）。
            category: 重新发布时直接指定的分类，形如
                ``{"catId", "catName", "channelCatId", "tbCatId"}``。
            address: 重新发布时直接指定的发货地址（``get_default_location`` 返回的单条），
                避免每次都取默认地址。

        Returns:
            发布接口完整响应（成功时 ``ret`` 含 ``SUCCESS``）。
        """
        if not (images_path or image_urls):
            raise PublishApiError(
                "mtop.idle.pc.idleitem.publish", ["images_path 与 image_urls 至少提供其一"]
            )

        images_info = await self._upload_images(images_path, image_urls)

        # 编辑 / 重新发布模式：优先用调用方传入的分类与地址
        if item_id and category:
            channel_res_data = {
                "cardList": [],
                "categoryPredictResult": {
                    "catId": category.get("catId", ""),
                    "catName": category.get("catName", ""),
                    "channelCatId": category.get("channelCatId", ""),
                    "tbCatId": category.get("tbCatId", ""),
                },
            }
        else:
            channel_res = await self.get_publish_channel(goods_desc, images_info)
            channel_res_data = (channel_res.get("data") or {})

        if item_id and address:
            location = address
        else:
            location_res = await self.get_default_location()
            # 默认值只在 key 不存在时生效，返回空列表时这里不能取 [0]
            addresses = (location_res.get("data") or {}).get("commonAddresses") or [{}]
            location = addresses[0] if addresses else {}

        # 组装发布 body
        data: dict[str, Any] = {
            "freebies": False,
            "itemTypeStr": "b",
            "quantity": "1",
            "simpleItem": "true",
            "imageInfoDOList": [],
            "itemTextDTO": {
                "desc": goods_desc,
                "title": goods_desc,
                "titleDescSeparate": False,
            },
            "itemLabelExtList": [],
            "itemPriceDTO": {},
            "userRightsProtocols": [
                {
                    "enable": False,
                    "serviceCode": "SKILL_PLAY_NO_MIND",
                }
            ],
            "itemPostFeeDTO": {
                "canFreeShipping": False,
                "supportFreight": False,
                "onlyTakeSelf": False,
            },
            "itemAddrDTO": {},
            "defaultPrice": False,
            "itemCatDTO": {},
            "uniqueCode": str(int(time.time() * 1000)),
            "sourceId": "pcMainPublish",
            "bizcode": "pcMainPublish",
            "publishScene": "pcMainPublish",
        }

        # 重新发布：带上 itemId 并切换为编辑场景
        if item_id:
            data["itemId"] = str(item_id)
            data["sourceId"] = "editPublish"
            data["bizcode"] = "editPublish"
            data["publishScene"] = "editPublish"

        for image_info in images_info:
            data["imageInfoDOList"].append({
                "extraInfo": {
                    "isH": "false",
                    "isT": "false",
                    "raw": "false",
                },
                "isQrCode": False,
                "url": image_info["url"],
                "heightSize": image_info["height"],
                "widthSize": image_info["width"],
                "major": True,
                "type": 0,
                "status": "done",
            })

        # 运费
        choice = ds.get("choice")
        if choice == "包邮":
            data["itemPostFeeDTO"]["canFreeShipping"] = True
            data["itemPostFeeDTO"]["supportFreight"] = True
        elif choice == "按距离计费":
            data["itemPostFeeDTO"]["supportFreight"] = True
            data["itemPostFeeDTO"]["templateId"] = "-100"
        elif choice == "一口价":
            data["itemPostFeeDTO"]["supportFreight"] = True
            data["itemPostFeeDTO"]["postPriceInCent"] = str(int(float(ds.get("post_price") or 0) * 100))
            data["itemPostFeeDTO"]["templateId"] = "0"
        elif choice == "无需邮寄":
            data["itemPostFeeDTO"]["templateId"] = "0"
        else:
            # 与网络/业务失败保持同一种异常，调用方才能只 catch 一次
            raise PublishApiError(
                "mtop.idle.pc.idleitem.publish", [f"无效的运费选项: {choice}"]
            )

        if ds.get("can_self_pickup"):
            # onlyTakeSelf 属于 itemPostFeeDTO，写在顶层服务端读不到
            data["itemPostFeeDTO"]["onlyTakeSelf"] = True

        # 价格
        if price:
            if float(price.get("current_price", 0)) > 0:
                data["itemPriceDTO"]["priceInCent"] = str(int(float(price["current_price"]) * 100))
            if float(price.get("original_price", 0)) > 0:
                data["itemPriceDTO"]["origPriceInCent"] = str(int(float(price["original_price"]) * 100))
        else:
            data["defaultPrice"] = True

        # 标签（仅新建走分类预测时填充）
        if item_id and category:
            pass  # 重新发布不重新选标签
        else:
            for card in channel_res_data.get("cardList", []):
                card_data = card.get("cardData", {})
                values_list = card_data.get("valuesList", []) if "valuesList" in card_data else []
                for card_value in values_list:
                    if card_value.get("isClicked"):
                        data["itemLabelExtList"].append({
                            "channelCateName": card_value["catName"],
                            "valueId": None,
                            "channelCateId": card_value["channelCatId"],
                            "valueName": None,
                            "tbCatId": card_value["tbCatId"],
                            "subPropertyId": None,
                            "labelType": "common",
                            "subValueId": None,
                            "labelId": None,
                            "propertyName": card_data["propertyName"],
                            "isUserClick": "1",
                            "isUserCancel": None,
                            "from": "newPublishChoice",
                            "propertyId": card_data["propertyId"],
                            "labelFrom": "newPublish",
                            "text": card_value["catName"],
                            "properties": (
                                f'{card_data["propertyId"]}##{card_data["propertyName"]}'
                                f':{card_value["channelCatId"]}##{card_value["catName"]}'
                            ),
                        })
                        break

        predict = channel_res_data.get("categoryPredictResult", {})
        data["itemCatDTO"] = {
            "catId": str(predict.get("catId", "")),
            "catName": str(predict.get("catName", "")),
            "channelCatId": str(predict.get("channelCatId", "")),
            "tbCatId": str(predict.get("tbCatId", "")),
        }

        if location:
            data["itemAddrDTO"] = {
                "area": location.get("area", ""),
                "city": location.get("city", ""),
                "divisionId": location.get("divisionId", ""),
                "gps": f"{location.get('longitude', '')},{location.get('latitude', '')}",
                "poiId": location.get("poiId", ""),
                "poiName": location.get("poi", ""),
                "prov": location.get("prov", ""),
            }

        return await self._call(
            "mtop.idle.pc.idleitem.publish",
            data,
            version="1.0",
            spm_pre="a21ybx.home.sidebar.1.46413da6EPl7v5",
            log_id="46413da6EPl7v5",
            value_type=None,
        )


if __name__ == "__main__":
    import os

    # 用法示例：需要把 COOKIE 环境变量填成有效的闲鱼登录 cookie
    cookie = os.environ.get("XY_COOKIE", "")
    if not cookie:
        print("请设置环境变量 XY_COOKIE 为有效 cookie 后运行")
        raise SystemExit(1)

    async def _demo():
        api = XianyuItemPublishAPI(cookie_id="demo", cookies_str=cookie)
        async with api:
            res = await api.publish(
                images_path=[r"D:\Desktop\logo.jpg"],
                goods_desc="测试发布111222",
                price=None,
                ds={"choice": "一口价", "post_price": 0.01, "can_self_pickup": True},
            )
        print(json.dumps(res, indent=4, ensure_ascii=False))

    asyncio.run(_demo())
