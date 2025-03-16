from typing import Tuple, Any, Union

from playwright.async_api import async_playwright, Page
from playwright.async_api import Error as PlaywrightError
from playwright.sync_api import ViewportSize
from urllib.parse import urlparse, urljoin
from beartype import beartype
from difflib import SequenceMatcher

from PIL import Image
from io import BytesIO
import asyncio
import base64
from .actions import Action, ActionTypes
from .build_tree import HTMLTree
from .utils import stringfy_value

from webcanvas.agent.Prompt import *
from webcanvas.logs import logger
import importlib.resources as resources

from playwright.async_api import Browser as PlaywrightBrowser
from webcanvas.agent.Environment.html_env.context import BrowserContextConfig, BrowserContext, BrowserSession

class ActionExecutionError(Exception):
    """Custom action execution exception class"""

    def __init__(self, action_type, message, selector=None):
        self.action_type = action_type
        self.message = message
        self.selector = selector
        super().__init__(message)


class SelectorExecutionError(Exception):
    def __init__(self, message, selector=None):
        super().__init__(message)


class AsyncHTMLEnvironment:
    @beartype
    def __init__(
        self,
        mode="dom",
        max_page_length: int = 8192,
        headless: bool = True,
        slow_mo: int = 0,
        current_viewport_only: bool = False,
        viewport_size: ViewportSize = {"width": 1280, "height": 720},
        save_trace_enabled: bool = False,
        sleep_after_execution: float = 0.0,
        locale: str = "en-US",
        use_vimium_effect=True,
        hide_unexpanded_elements=True,
        proxy_server=None
    ):
        self.use_vimium_effect = use_vimium_effect
        self.mode = mode
        self.headless = headless
        self.slow_mo = slow_mo
        self.current_viewport_only = current_viewport_only
        self.reset_finished = False
        self.viewport_size = viewport_size
        self.save_trace_enabled = save_trace_enabled
        self.sleep_after_execution = sleep_after_execution
        self.tree = HTMLTree()
        self.locale = locale
        self.context = None
        self.browser = None
        self.proxy = {"server": proxy_server} if proxy_server else None
        self.config = BrowserContextConfig()
        self.browser_context = BrowserContext()
    
    async def get_browser(self) -> PlaywrightBrowser:
        if self.browser is None:
            self.browser = await self.setup()
        return self.browser

    async def _initialize_session(self) -> BrowserSession:
        """Initialize the browser session"""
        logger.debug('Initializing browser context')

        browser = await self.get_browser()
        self.browser_context.browser = browser
        context = await self.browser_context._get_context(browser)
        self._page_event_handler = None

        # Get or create a page to use
        pages = context.pages

        self.browser_context.session = BrowserSession(
            context=context,
            cached_state=None,
        )

        active_page = None

        # If no target ID or couldn't find it, use existing page or create new
        if not active_page:
            if pages:
                active_page = pages[0]
                logger.debug('Using existing page')
            else:
                active_page = await context.new_page()
                logger.debug('Created new page')

        # Bring page to front
        await active_page.bring_to_front()
        await active_page.wait_for_load_state('load')

        return self.browser_context.session

    async def page_on_handler(self, page):
        self.page = page
    
    async def setup(self, start_url: str) -> PlaywrightBrowser:
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            # channel='chrome', # use personal chrome
            # firefox_user_prefs={"media.eme.enabled": False, "browser.eme.ui.enabled": False}, # disable DRM
            headless=self.headless,
            slow_mo=self.slow_mo,
            proxy=self.proxy,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--disable-background-timer-throttling',
                '--disable-popup-blocking',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--disable-window-activation',
                '--disable-focus-on-load',
                '--no-first-run',
                '--no-default-browser-check',
                '--no-startup-window',
                '--window-position=0,0',
                # disable web security
                '--disable-web-security',
				'--disable-site-isolation-trials',
				'--disable-features=IsolateOrigins,site-per-process',
            ]
        )
        self.context = await self.browser.new_context(
            viewport=self.viewport_size,
            device_scale_factor=1,
            locale=self.locale,
            proxy=self.proxy,
        )
        self.context.on("page", self.page_on_handler)
        if start_url:
            self.page = await self.context.new_page()
            await self.page.wait_for_load_state()
            # await self.page.set_viewport_size({"width": 1080, "height": 720}) if not self.mode == "dom" else None
            await self.page.goto(start_url, timeout=20000)
            await self.update_html_content()
        else:
            self.page = await self.context.new_page()
            # await self.page.set_viewport_size({"width": 1080, "height": 720}) if not self.mode == "dom" else None
            self.html_content = await self.page.content()
        await self._initialize_session()
        # self.last_page = self.page
        return self.browser

    async def update_html_content(self):
        await self.page.wait_for_load_state("load")
        await self.page.wait_for_timeout(2000)
        self.html_content = await self.page.content()

    async def _build_html_tree(self) -> str:
        """evaluate the js code to build the html tree"""
        self.tree.__init__()
        js_code = resources.read_text('webcanvas.agent.Environment.html_env', 'buildDomTree.js')
        try:
            eval_page = await self.page.evaluate(js_code)
        except Exception as e:
            logger.error('Error evaluating JavaScript: %s', e)
            raise
        # logger.info("successfully execute js code")
        return self.tree._build_dom_tree(eval_page)

    async def _get_obs(self) -> Union[str, Tuple[str, str]]:
        logger.info("_get_obs")
        observation = ""
        observation_VforD = ""
        try:
            if not self.html_content.strip():
                await self.get_html_content()
            tab_name = await self.page.title()
            dom_tree = await self._build_html_tree()
            logger.info("-- Successfully fetch html content")
            observation = f"current web tab name is \'{tab_name}\'\n" + dom_tree
            if self.mode in ["d_v", "dom_v_desc", "vision_to_dom"]:
                observation_VforD = await self.capture()
        except Exception as e:
            logger.error(f"-- Failed to fetch html content, error occurred: {e}")

        if self.mode in ["d_v", "dom_v_desc", "vision_to_dom"]:
            is_valid, message = is_valid_base64(observation_VforD)
            logger.info("Successfully fetch html content with observation_VforD:", message)

        return (observation, observation_VforD) if self.mode in ["d_v", "dom_v_desc", "vision_to_dom"] else observation

    async def reset(self, start_url: str = ""):
        await self.setup(start_url)

    async def click(self, action):
        session = self.browser_context.session
        element_node = self.tree.pruningTreeNode[action["element_id"]]
        initial_pages = len(session.context.pages)

        if await self.browser_context.is_file_uploader(element_node):
            msg = f'Index {self.tree.nodeDict.index(action["element_id"])} - has an element which opens file upload dialog. To upload files please use a specific function to upload files '
            logger.info(msg)
            return
        msg = None
        try:
            download_path = await self.browser_context._click_element_node(element_node, self.tree.pruningTreeNode)
            if download_path:
                msg = f'💾  Downloaded file to {download_path}'
            else:
                msg = f'🖱️  Clicked button with index {self.tree.nodeDict.index(action["element_id"])}'

            # logger.info(msg)
            logger.debug(f'Element xpath: {element_node.get("xpath")}')
            if len(session.context.pages) > initial_pages:
                new_tab_msg = 'New tab opened - switching to it'
                msg += f' - {new_tab_msg}'
                logger.info(new_tab_msg)
                await self.browser_context.switch_to_tab(-1)
            await self.update_html_content()
            return 
        except Exception as e:
            logger.warning(f'Element not clickable with index {self.tree.nodeDict.index(action["element_id"])} - most likely the page changed')
            return 
    
    async def goto(self, action):
        await self.browser_context.navigate_to(action['url'])
        await self.update_html_content()

    async def fill_search(self, action):
        try:
            label, element_id, _ = self.tree.resolve_element_semantics(
                self.tree.elementNodes[action["element_id"]])
            action.update({"element_id": element_id,
                           "element_name": label})
            selector, xpath = self.tree.get_selector_and_xpath(
                action["element_id"])
        except Exception as e:
            logger.error(
                f"selector:{selector},label_name:{label},element_id: {element_id},error ({e}) in fill_search action.")
        try:
            value = stringfy_value(action['fill_text'])
            await self.page.locator(selector).fill(value)
            await self.page.locator(selector).press("Enter")
            await self.update_html_content()
        except:
            try:
                selector = rf"{selector}"
                value = stringfy_value(action['fill_text'])
                await self.page.evaluate(f'''
                    (selector) => {{
                        var element = document.querySelector(selector);
                        if (element) {{
                            element.value = '{value}';
                            element.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            element.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter' }}));
                        }}
                    }}
                ''', selector)
                await self.update_html_content()
            except Exception as e:
                raise e
        
    async def fill_form(self, action):
        element_node = self.tree.elementNodes[action["element_id"]]
        await self.browser_context._input_text_element_node(element_node, action["fill_text"], self.tree.pruningTreeNode)
        await self.update_html_content()

    async def search(self, action):
        page = await self.browser_context.get_current_page()
        await page.goto(f'https://www.google.com/search?q={action["fill_text"]}&udm=14')
        await self.update_html_content()

    async def go_back_last_page(self, action):
        await self.browser_context.go_back()
        await self.update_html_content()

    async def select_option(self, action):
        try:
            label, element_id, _ = self.tree.resolve_element_semantics(
                self.tree.elementNodes[action["element_id"]])
            action.update({"element_id": element_id,
                           "element_name": label})
            selector, xpath = self.tree.get_selector_and_xpath(
                action["element_id"])
        except Exception as e:
            logger.error(
                f"selector:{selector},label_name:{label},element_id: {element_id},error ({e}) in select_option action.")
        try:
            selector = rf"{selector}"
            optgroup_values = await self.page.evaluate(f'''(selector) => {{
                var values = [];
                var selectElement = document.querySelector(selector);
                var options = selectElement.querySelectorAll('option');
                for (var option of options) {{
                    values.push(option.innerText);
                }}
                var optgroups = selectElement.querySelectorAll('optgroup');
                for (var optgroup of optgroups) {{
                    var options = optgroup.querySelectorAll('option');
                    for (var option of options) {{
                        values.push(option.innerText);
                    }}   
                }}
                return values;
            }}''', selector)
            best_option = [-1, "", -1]
            for i, option in enumerate(optgroup_values):
                similarity = SequenceMatcher(
                    None, option, action['fill_text']).ratio()
                if similarity > best_option[2]:
                    best_option = [i, option, similarity]
            await self.page.evaluate(f'''(selector) => {{
                var option = document.querySelector(selector); 
                if (option) {{
                    option.selected = true; 
                    option.parentElement.dispatchEvent(new Event('change'));
                }}
            }}''', selector)
            await self.update_html_content()
        except Exception as e:
            raise e

    async def hover(self, action):
        try:
            label, element_id, _ = self.tree.resolve_element_semantics(
                self.tree.elementNodes[action["element_id"]])
            action.update({"element_id": element_id,
                           "element_name": label})
            selector, xpath = self.tree.get_selector_and_xpath(
                action["element_id"])
        except Exception as e:
            logger.error(
                f"selector:{selector},label_name:{label},element_id: {element_id},error ({e}) in hover action.")
        try:
            await self.page.hover(selector)
            await self.update_html_content()
        except:
            hover = '''() => {
                        var element = document.querySelector('%s');
                        if (element) {
                            element.dispatchEvent(new Event('mouseover', { bubbles: true }));
                        }
                    }
                ''' % selector
            await self.page.evaluate(hover)
            await self.update_html_content()

    async def scroll_down(self):
        try:
            total_height = await self.page.evaluate("document.body.scrollHeight")
            viewport_height = await self.page.evaluate("window.innerHeight")
            if total_height < viewport_height:
                await self.page.evaluate("window.scrollBy(0, 500)")
                await self.update_html_content()
            current_scroll = await self.page.evaluate("window.pageYOffset")
            remaining_height = total_height - current_scroll - viewport_height
            if remaining_height <= viewport_height:
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            else:
                scroll_amount = current_scroll + viewport_height * 0.75
                await self.page.evaluate(f"window.scrollTo(0, {scroll_amount})")
            await self.update_html_content()
        except:
            await self.page.mouse.wheel(0, 100)
            await self.update_html_content()

    async def scroll_up(self):
        try:
            viewport_height = await self.page.evaluate("window.innerHeight")
            current_scroll = await self.page.evaluate("window.pageYOffset")
            if current_scroll > 0:
                if current_scroll < viewport_height:
                    scroll_amount = 0
                else:
                    scroll_amount = current_scroll - viewport_height / 2
                await self.page.evaluate(f"window.scrollTo(0, {scroll_amount})")
            await self.update_html_content()
        except:
            await self.page.mouse.wheel(0, -100)
            await self.update_html_content()

    async def execute_action(self, action: Action) -> Union[str, Tuple[str, str]]:
        """
        """
        if "element_id" in action and action["element_id"] != 0:
            # logger.info(f'action["element_id"]:{action["element_id"]}')
            # logger.info(
            #     f'tree.nodeDict[action["element_id"]]:{self.tree.nodeDict[action["element_id"]]}')
            action["element_id"] = self.tree.nodeDict[action["element_id"]]
            element_value = self.tree.get_element_value(action["element_id"])
        match action["action_type"]:
            case ActionTypes.CLICK:
                try:
                    await self.click(action)
                except Exception as e:
                    error_message = f"Failed to execute click [{action['element_id']}, {element_value}] action. An error({e}) occur"
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case ActionTypes.GOTO:
                try:
                    await self.goto(action)
                except Exception as e:
                    error_message = f"Failed to execute goto [{action['url']}] action. An error({e}) occur."
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case ActionTypes.FILL_SEARCH:
                try:
                    await self.fill_search(action)
                except Exception as e:
                    error_message = f"Failed to execute fill_form [{action['element_id']},{action['fill_text']}] action. An error({e}) occur."
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case ActionTypes.FILL_FORM:
                try:
                    await self.fill_form(action)
                except Exception as e:
                    error_message = f"Failed to execute fill_form [{action['element_id']},{action['fill_text']}] action. An error({e}) occur."
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case ActionTypes.GOOGLE_SEARCH:
                try:
                    await self.search(action)
                except Exception as e:
                    error_message = f"Failed to execute google_search[{action['fill_text']}] action. An error({e}) occur."
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case ActionTypes.GO_BACK:
                try:
                    await self.go_back_last_page(action)
                except Exception as e:
                    error_message = f"Failed to execute go_back action. An error({e}) occur."
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case ActionTypes.SELECT_OPTION:
                try:
                    await self.select_option(action)
                except Exception as e:
                    error_message = f"Failed to execute select_option [{action['element_id']},{action['fill_text']}] action. An error({e}) occur."
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case ActionTypes.HOVER:
                try:
                    await self.hover(action)
                except Exception as e:
                    error_message = f"Failed to execute hover [{action['element_id']},{element_value}] action. An error({e}) occur"
                    # print(error_message)
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case ActionTypes.SCROLL_DOWN:
                try:
                    await self.scroll_down()
                except Exception as e:
                    error_message = f"Failed to execute scroll_down action. An error({e}) occur"
                    # print(error_message)
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case ActionTypes.SCROLL_UP:
                try:
                    await self.scroll_up()
                except Exception as e:
                    error_message = f"Failed to execute scroll_up action. An error({e}) occur"
                    # print(error_message)
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case ActionTypes.NONE:
                try:
                    await self.update_html_content()
                except Exception as e:
                    error_message = f"An error({e}) occur"
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case ActionTypes.CACHE_DATA:
                try:
                    await self.update_html_content()
                except Exception as e:
                    error_message = f"An error({e}) occur"
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case ActionTypes.GET_FINAL_ANSWER:
                try:
                    await self.update_html_content()
                except Exception as e:
                    error_message = f"An error({e}) occur"
                    raise ActionExecutionError(
                        action['action_type'], error_message) from e
            case _:
                raise ValueError(
                    f"Unknown action type {action['action_type']}"
                )

    async def get_page(self, element_id: int) -> Tuple[Page, str]:
        try:
            # selector = self.tree.get_selector(element_id)
            selector = self.tree._get_selector(self.tree.elementNodes[element_id])
        except:
            selector = ""
        return self.page, selector

    async def close(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    @staticmethod
    def encode_and_resize(image):
        img_res = 1080
        w, h = image.size
        img_res_h = int(img_res * h / w)
        image = image.resize((img_res, img_res_h))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        encoded_image = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return encoded_image

    async def capture(self) -> Image:
        if not self.page:
            raise ValueError("Page not initialized or loaded.")
        screenshot_bytes = ""
        for i in range(6):
            try:
                screenshot_bytes = await self.page.screenshot()
                break
            except:
                logger.info(
                    "Capture screenshot_bytes failed for", i+1, "times")
                await asyncio.sleep(1)
        screenshot = Image.open(BytesIO(screenshot_bytes)).convert("RGB")
        encoded_screenshot = self.encode_and_resize(screenshot)
        is_valid, message = is_valid_base64(
            encoded_screenshot)
        return encoded_screenshot

    @staticmethod
    async def is_valid_element(page: Page, selector: str):
        element = await page.query_selector(selector)
        if element:
            if await element.is_visible() is False:
                return False
            elif await element.is_hidden() is True:
                return False
        else:
            return False
        return True

    async def load_page_with_retry(self, url, retries=3, delay=5):
        for attempt in range(retries):
            try:
                await self.page.goto(url, timeout=20000)
                await self.page.wait_for_timeout(2000)
                return
            except Exception as e:
                if "Timeout" in str(e):
                    if attempt < retries - 1:
                        logger.info(
                            f"Timeout occurred, retrying in {delay * attempt} seconds...")
                        await asyncio.sleep(delay * (attempt + 1))
                    else:
                        logger.error(
                            f"Max retries {retries} reached, giving up.")
                        raise

    async def get_obs(self) -> str:
        """Get the current state of the browser"""
        await self.browser_context._wait_for_page_and_frames_load()
        session = self.browser_context.session
        session.cached_state = await self._update_state()
        logger.info("-- Successfully fetch html content")
        if self.config.cookies_file:
            asyncio.create_task(self.browser_context.save_cookies())
        return session.cached_state

    async def _update_state(self) -> str:
        """Update and return state."""
        session = self.browser_context.session

        # Check if current page is still valid, if not switch to another available page
        try:
            page = await self.browser_context.get_current_page()
            # Test if page is still accessible
            await page.evaluate('1')
        except Exception as e:
            logger.debug(f'Current page is no longer accessible: {str(e)}')
            # Get all available pages
            pages = session.context.pages
            if pages:
                page = await self.browser_context._get_current_page(session)
                logger.debug(f'Switched to page: {await page.title()}')
            else:
                raise Exception('Browser closed: no valid pages available')

        try:
            if not self.html_content.strip():
                await self.browser_context.get_page_html()
            content = await self._build_html_tree()
            self.current_state = content
            self.browser_context.state = content
            return self.current_state
        except Exception as e:
            logger.error(f'Failed to update state: {str(e)}')
            # Return last known good state if available
            if hasattr(self, 'current_state'):
                return self.current_state
            raise