# -*- coding: utf-8 -*-
"""
小红书一体化爬虫

运行逻辑：
1. 用户输入关键词。
2. 打开小红书搜索结果页，爬取搜索结果中的帖子链接。
3. 根据帖子链接爬取每条帖子的详情信息、评论信息。
4. 根据帖子详情中的作者主页链接，询问用户是否继续爬取作者主页详细信息。

依赖：
    pip install DrissionPage
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

try:
    from DrissionPage import ChromiumOptions, ChromiumPage
    from DrissionPage.common import Keys
except ImportError as exc:
    raise SystemExit("缺少依赖 DrissionPage，请先执行：pip install DrissionPage") from exc


BASE_URL = "https://www.xiaohongshu.com"
DEFAULT_CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

SCROLL_PAUSE = 2.0
MAX_SCROLL_ATTEMPTS = 50
MAX_COMMENTS_PER_POST = 10
BATCH_WRITE_SIZE = 10


def sanitize_filename(name: str, default: str = "未命名") -> str:
    """把关键词或标题整理成适合作为文件名的字符串。"""
    name = (name or "").strip() or default
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", "_", name)
    return name[:100] or default


def normalize_url(href: str | None) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return BASE_URL + href
    return href


def write_rows(rows: list[dict], csv_path: Path) -> None:
    if not rows:
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    header = not csv_path.exists()
    fieldnames = list(rows[0].keys())
    with csv_path.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        if header:
            writer.writeheader()
        writer.writerows(rows)


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    answer = input(f"{prompt}（{suffix}）：").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes", "是", "继续", "1"}


def ask_positive_int(prompt: str, default: int) -> int:
    answer = input(f"{prompt}（默认 {default}）：").strip()
    if not answer:
        return default
    try:
        value = int(answer)
        return value if value > 0 else default
    except ValueError:
        print(f"输入不是有效数字，已使用默认值 {default}。")
        return default


def ask_non_negative_int(prompt: str, default: int) -> int:
    answer = input(f"{prompt}（默认 {default}）：").strip()
    if not answer:
        return default
    try:
        value = int(answer)
        return value if value >= 0 else default
    except ValueError:
        print(f"输入不是有效数字，已使用默认值 {default}。")
        return default


def create_browser() -> ChromiumPage:
    co = ChromiumOptions()
    if os.path.exists(DEFAULT_CHROME_PATH):
        co.set_browser_path(DEFAULT_CHROME_PATH)
    else:
        print(f"未找到默认 Chrome 路径：{DEFAULT_CHROME_PATH}，将使用 DrissionPage 默认浏览器配置。")
    return ChromiumPage(co)


def wait_for_login(dp: ChromiumPage) -> None:
    dp.get(BASE_URL)
    input("请在弹出的浏览器中手动完成登录，登录成功后回到这里按回车继续...")


def build_search_url(keyword: str) -> str:
    return f"{BASE_URL}/search_result?keyword={quote(keyword)}&source=web_search_result_notes"


def has_search_result_links(dp: ChromiumPage) -> bool:
    try:
        return any("/search_result/" in (a.attr("href") or "") for a in dp.eles("tag:a"))
    except Exception:
        return False


def current_page_url(dp: ChromiumPage) -> str:
    return str(getattr(dp, "url", "") or "")


def open_search_page(dp: ChromiumPage, keyword: str) -> None:
    """打开关键词搜索结果页；确认搜索触发后才返回。"""
    search_url = build_search_url(keyword)
    print(f"正在打开搜索结果页：{search_url}")
    dp.get(search_url)
    time.sleep(5)

    if "/search_result" in current_page_url(dp) or has_search_result_links(dp):
        print(f"已进入搜索页：{current_page_url(dp)}")
        return

    print("直接打开搜索 URL 未确认成功，尝试使用页面搜索框。")
    dp.get(BASE_URL)
    time.sleep(3)

    try:
        search_box = None
        for selector in [
            "css:#search-input",
            "css:input[type='search']",
            "css:input[placeholder*='搜索']",
        ]:
            try:
                search_box = dp.ele(selector, timeout=2)
                if search_box:
                    break
            except Exception:
                continue

        if not search_box:
            raise RuntimeError("没有找到搜索输入框。")

        before_url = current_page_url(dp)
        try:
            search_box.click()
        except Exception:
            pass
        search_box.clear()
        time.sleep(0.3)
        search_box.input(keyword)
        time.sleep(0.5)
        search_box.input(Keys.ENTER)
        time.sleep(4)

        if current_page_url(dp) != before_url or "/search_result" in current_page_url(dp) or has_search_result_links(dp):
            print(f"搜索框搜索成功：{current_page_url(dp)}")
            return
    except Exception as exc:
        print(f"搜索框搜索失败：{exc}")

    print("尝试用 JavaScript 填写搜索框并触发回车。")
    keyword_js = json.dumps(keyword, ensure_ascii=False)
    dp.get(BASE_URL)
    time.sleep(3)
    dp.run_js(
        f"""
        const keyword = {keyword_js};
        const input = document.querySelector('#search-input, input[type="search"], input[placeholder*="搜索"]');
        if (input) {{
            input.focus();
            input.value = keyword;
            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
            input.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', code: 'Enter', bubbles: true }}));
            input.dispatchEvent(new KeyboardEvent('keyup', {{ key: 'Enter', code: 'Enter', bubbles: true }}));
        }}
        """
    )
    time.sleep(4)

    if "/search_result" in current_page_url(dp) or has_search_result_links(dp):
        print(f"JavaScript 搜索成功：{current_page_url(dp)}")
        return

    print("页面搜索没有成功触发，最后再次打开搜索 URL。")
    dp.get(search_url)
    time.sleep(5)
    print(f"当前页面：{current_page_url(dp)}")


def scrape_search_results(
    dp: ChromiumPage,
    keyword: str,
    max_scroll_attempts: int = MAX_SCROLL_ATTEMPTS,
) -> list[dict]:
    print(f"\n========== 正在搜索关键词：{keyword} ==========")
    open_search_page(dp, keyword)

    seen_links: set[str] = set()
    results: list[dict] = []

    for attempt in range(1, max_scroll_attempts + 1):
        new_count = 0
        a_tags = dp.eles("tag:a")

        for a_tag in a_tags:
            try:
                href = a_tag.attr("href") or ""
                if "/search_result/" not in href:
                    continue

                full_url = normalize_url(href)
                if not full_url or full_url in seen_links:
                    continue

                seen_links.add(full_url)
                results.append({"关键词": keyword, "帖子链接": full_url})
                new_count += 1
                print(f"[搜索结果] {full_url}")
            except Exception as exc:
                print(f"跳过一个异常链接：{exc}")

        print(f"第 {attempt} 次滚动，新增 {new_count} 条链接，累计 {len(results)} 条。")

        is_bottom = dp.run_js(
            "return (window.innerHeight + window.scrollY) >= document.body.scrollHeight - 2"
        )
        if is_bottom:
            print("页面已滚动到底部，结束搜索结果爬取。")
            break

        dp.run_js("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(SCROLL_PAUSE)

    return results


def extract_tags_from_content(full_text: str) -> tuple[str, str]:
    full_text = (full_text or "").strip()
    if not full_text:
        return "", ""

    hashtags = re.findall(r"#([^\s#]+)", full_text)
    content = full_text
    for tag in hashtags:
        content = content.replace(f"#{tag}", "")
    return content.strip(), ", ".join(hashtags)


def get_first_text(page_or_ele, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            ele = page_or_ele.ele(selector, timeout=1)
            if ele and ele.text:
                return ele.text.strip()
        except Exception:
            continue
    return ""


def get_first_attr(page_or_ele, selectors: list[str], attr_name: str) -> str:
    for selector in selectors:
        try:
            ele = page_or_ele.ele(selector, timeout=1)
            if ele:
                value = ele.attr(attr_name)
                if value:
                    return value.strip()
        except Exception:
            continue
    return ""


def scrape_post_detail(dp: ChromiumPage, url: str, keyword: str) -> dict:
    dp.get(url)
    time.sleep(3)

    try:
        dp.wait.ele_displayed("css:#noteContainer", timeout=10)
    except Exception:
        pass

    user_home = get_first_attr(
        dp,
        [
            "css:#noteContainer a[href*='/user/profile/']",
            "css:a[href*='/user/profile/']",
            "xpath://*[@id='noteContainer']/div[4]/div[1]/div/div[1]/a[1]",
        ],
        "href",
    )
    user_home = normalize_url(user_home)

    user = get_first_text(dp, ["css:span.username", "css:.author .name", "css:.user-name"])
    title = get_first_text(dp, ["css:#detail-title", "css:.title"])
    if not title:
        title = "无标题"

    full_text = get_first_text(dp, ["css:#desc", "css:.desc", "css:.note-content"])
    content, tags = extract_tags_from_content(full_text)

    if not tags:
        try:
            tag_eles = dp.eles("css:a#hash-tag")
            tags = ", ".join(t.text.strip().lstrip("#") for t in tag_eles if t.text.strip())
        except Exception:
            tags = ""

    post_time = ""
    post_location = ""
    date_text = get_first_text(dp, ["css:.date", "css:.publish-time"])
    if date_text:
        parts = date_text.split()
        if len(parts) > 1 and re.fullmatch(r"[\u4e00-\u9fa5]+", parts[-1]):
            post_location = parts[-1]
            post_time = " ".join(parts[:-1])
        else:
            post_time = date_text

    like_count = get_first_text(dp, ["css:.engage-bar-container .like-wrapper .count"])
    collect_count = get_first_text(dp, ["css:.engage-bar-container .collect-wrapper .count"])
    comment_count = get_first_text(dp, ["css:.engage-bar-container .chat-wrapper .count"])

    image_urls: list[str] = []
    try:
        img_elements = dp.eles("css:div.swiper-slide:not(.swiper-slide-duplicate) img.note-slider-img")
        for img in img_elements:
            src = img.attr("src")
            if src:
                image_urls.append(src)
    except Exception:
        pass

    return {
        "关键词": keyword,
        "详情链接": url,
        "用户": user,
        "用户主页": user_home,
        "标题": title,
        "正文": content,
        "标签": tags,
        "发布时间": post_time,
        "发布地点": post_location,
        "点赞数": like_count or "0",
        "收藏数": collect_count or "0",
        "评论数": comment_count or "0",
        "图片链接": "\n".join(image_urls),
    }


def scrape_comments(
    dp: ChromiumPage,
    post_url: str,
    post_title: str,
    max_comments: int = MAX_COMMENTS_PER_POST,
) -> list[dict]:
    if max_comments <= 0:
        return []

    comments: list[dict] = []
    seen_comments: set[tuple[str, str]] = set()

    scroll_area = None
    for selector in ["css:.note-scroller", "css:.comments-el", "css:.comment-list"]:
        try:
            scroll_area = dp.ele(selector, timeout=1)
            if scroll_area:
                break
        except Exception:
            continue

    if not scroll_area:
        return comments

    try:
        scroll_area_selector = scroll_area.css_path
    except Exception:
        scroll_area_selector = ".note-scroller"

    for _ in range(20):
        if len(comments) >= max_comments:
            break

        try:
            dp.run_js(
                f"let el = document.querySelector({scroll_area_selector!r}); "
                "if (el) { el.scrollTop = el.scrollHeight; }"
            )
        except Exception:
            pass
        time.sleep(2)

        try:
            comment_blocks = dp.eles("css:div.comment-item")
        except Exception:
            comment_blocks = []

        before_count = len(seen_comments)
        for block in comment_blocks:
            commenter = get_first_text(block, ["css:.author .name", "css:.name"])
            comment_text = get_first_text(block, ["css:.content .note-text", "css:.note-text", "css:.content"])
            if not commenter or not comment_text:
                continue

            comment_id = (commenter, comment_text)
            if comment_id in seen_comments:
                continue

            seen_comments.add(comment_id)
            comment_time = get_first_text(block, ["css:.info .date > span:first-child", "css:.date"])
            comment_location = get_first_text(block, ["css:.info .location", "css:.location"])
            comment_like = get_first_text(block, ["css:.interactions .like .count", "css:.like .count"])

            comments.append(
                {
                    "帖子标题": post_title,
                    "评论用户": commenter,
                    "评论内容": comment_text,
                    "评论时间": comment_time,
                    "评论地点": comment_location,
                    "评论点赞数": comment_like or "0",
                    "详情链接": post_url,
                }
            )
            if len(comments) >= max_comments:
                break

        if len(seen_comments) == before_count:
            break

    return comments


def scrape_user_home(dp: ChromiumPage, url: str) -> dict:
    dp.get(url)
    time.sleep(3)

    nickname = get_first_text(dp, ["css:div.user-name", "css:.user-name"])

    red_id_text = get_first_text(dp, ["css:span.user-redId"])
    red_id = red_id_text.replace("小红书号：", "").replace("小红书号:", "").strip()

    ip_text = get_first_text(dp, ["css:span.user-IP"])
    ip_location = ip_text.replace("IP属地：", "").replace("IP属地:", "").strip()

    description = get_first_text(dp, ["css:div.user-desc", "css:.user-desc"])

    tags = []
    try:
        tag_divs = dp.eles("css:div.user-tags div.tag-item")
        tags = [tag.text.strip() for tag in tag_divs if tag.text.strip()]
    except Exception:
        pass

    follows = ""
    fans = ""
    likes = ""
    try:
        data_spans = dp.eles("css:div.user-interactions > div")
        if len(data_spans) > 0:
            follows = get_first_text(data_spans[0], ["tag:span"])
        if len(data_spans) > 1:
            fans = get_first_text(data_spans[1], ["tag:span"])
        if len(data_spans) > 2:
            likes = get_first_text(data_spans[2], ["tag:span"])
    except Exception:
        pass

    gender = ""
    try:
        gender_icon = dp.ele("css:.user-tags .gender use", timeout=1)
        if gender_icon:
            href = gender_icon.attr("xlink:href") or gender_icon.attr("href") or ""
            if href == "#male":
                gender = "男"
            elif href == "#female":
                gender = "女"
    except Exception:
        pass

    return {
        "用户主页": url,
        "昵称": nickname,
        "小红书号": red_id,
        "性别": gender,
        "IP属地": ip_location,
        "简介": description,
        "标签": ", ".join(tags),
        "关注数": follows,
        "粉丝数": fans,
        "获赞与收藏数": likes,
    }


def main() -> None:
    keyword = input("请输入要搜索的小红书关键词：").strip()
    if not keyword:
        raise ValueError("关键词不能为空。")

    safe_keyword = sanitize_filename(keyword)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).resolve().parent / f"小红书_{safe_keyword}_爬取结果_{timestamp}"
    links_csv = output_dir / f"小红书_{safe_keyword}_搜索结果链接.csv"
    detail_csv = output_dir / f"小红书_{safe_keyword}_帖子详情.csv"
    comments_csv = output_dir / f"小红书_{safe_keyword}_帖子评论.csv"
    users_csv = output_dir / f"小红书_{safe_keyword}_用户主页信息.csv"

    max_scroll_attempts = ask_positive_int("搜索结果页最多滚动次数", MAX_SCROLL_ATTEMPTS)
    max_posts = ask_positive_int("最多爬取多少条帖子详情", 100)
    max_comments = ask_non_negative_int("每条帖子最多爬取多少条评论，输入 0 可跳过评论", MAX_COMMENTS_PER_POST)

    dp = create_browser()
    wait_for_login(dp)

    search_results = scrape_search_results(dp, keyword, max_scroll_attempts=max_scroll_attempts)
    write_rows(search_results, links_csv)
    print(f"\n搜索结果链接已保存：{links_csv}")

    if not search_results:
        print("没有抓到搜索结果链接，程序结束。")
        return

    detail_links = []
    seen = set()
    for row in search_results:
        link = row["帖子链接"]
        if link not in seen:
            seen.add(link)
            detail_links.append(link)
    detail_links = detail_links[:max_posts]

    detail_buffer: list[dict] = []
    comment_buffer: list[dict] = []
    user_home_links: list[str] = []

    for index, detail_url in enumerate(detail_links, start=1):
        print(f"\n[{index}/{len(detail_links)}] 正在爬取帖子详情：{detail_url}")
        try:
            detail = scrape_post_detail(dp, detail_url, keyword)
            detail_buffer.append(detail)
            if detail.get("用户主页"):
                user_home_links.append(detail["用户主页"])
            print(f"帖子详情爬取成功：{detail.get('标题', '')[:30]}")

            if max_comments > 0:
                comments = scrape_comments(dp, detail_url, detail.get("标题", ""), max_comments=max_comments)
                comment_buffer.extend(comments)
                print(f"本帖抓到 {len(comments)} 条评论。")
        except Exception as exc:
            print(f"帖子详情爬取失败：{detail_url}，原因：{exc}")

        if index % BATCH_WRITE_SIZE == 0 or index == len(detail_links):
            write_rows(detail_buffer, detail_csv)
            write_rows(comment_buffer, comments_csv)
            print(f"已批量写入：{len(detail_buffer)} 条帖子详情，{len(comment_buffer)} 条评论。")
            detail_buffer.clear()
            comment_buffer.clear()

    unique_user_links = []
    seen_users = set()
    for url in user_home_links:
        normalized = normalize_url(url)
        if normalized and normalized not in seen_users:
            seen_users.add(normalized)
            unique_user_links.append(normalized)

    print(f"\n共提取到 {len(unique_user_links)} 个去重后的作者主页链接。")
    if unique_user_links and ask_yes_no("是否继续爬取这些作者主页的详细信息", default=False):
        user_rows: list[dict] = []
        for index, user_url in enumerate(unique_user_links, start=1):
            print(f"[{index}/{len(unique_user_links)}] 正在爬取作者主页：{user_url}")
            try:
                user_rows.append(scrape_user_home(dp, user_url))
            except Exception as exc:
                print(f"作者主页爬取失败：{user_url}，原因：{exc}")

            if index % BATCH_WRITE_SIZE == 0 or index == len(unique_user_links):
                write_rows(user_rows, users_csv)
                print(f"已写入 {len(user_rows)} 条作者主页信息。")
                user_rows.clear()
    else:
        print("已按用户选择跳过作者主页详细信息爬取。")

    print("\n全部任务完成。输出目录：")
    print(output_dir)


if __name__ == "__main__":
    main()
