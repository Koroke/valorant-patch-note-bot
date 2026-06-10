import json
import os
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from google import genai


PATCH_LIST_URL = "https://playvalorant.com/en-us/news/tags/patch-notes/"
DATA_FILE = Path("latest_patch.json")

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


def load_latest_patch():
    if not DATA_FILE.exists():
        return {"url": "", "title": ""}

    with DATA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_latest_patch(title, url):
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "title": title,
                "url": url,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


def fetch_html(url):
    headers = {
        "User-Agent": "Mozilla/5.0 VALORANT Patch Note Discord Bot"
    }

    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return response.text


def find_latest_patch_note():
    html = fetch_html(PATCH_LIST_URL)
    soup = BeautifulSoup(html, "html.parser")

    links = soup.find_all("a", href=True)
    candidates = []

    for link in links:
        href = link.get("href", "")
        text = link.get_text(" ", strip=True)

        if "/news/game-updates/" in href and "patch-notes" in href:
            url = urljoin("https://playvalorant.com", href)

            title = text
            if not title:
                title = "VALORANT Patch Notes"

            candidates.append(
                {
                    "title": title,
                    "url": url,
                }
            )

    seen = set()
    unique_candidates = []

    for item in candidates:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique_candidates.append(item)

    if not unique_candidates:
        raise RuntimeError("最新パッチノートが見つかりませんでした。公式サイトの構造が変わった可能性があります。")

    return unique_candidates[0]


def extract_article_text(url):
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    article = soup.find("article")
    if article:
        text = article.get_text("\n", strip=True)
    else:
        text = soup.get_text("\n", strip=True)

    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            lines.append(line)

    cleaned_text = "\n".join(lines)

    return cleaned_text[:25000]


def summarize_with_gemini_once(title, article_text):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY が設定されていません。")

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
あなたはVALORANTプレイヤー向けのパッチノート要約Botです。
以下の公式パッチノート本文を、日本語でDiscord向けに要約してください。

条件：
- 10〜15行以内
- 初心者にも分かる言葉で書く
- ランクに影響しそうな変更を優先する
- エージェント、武器、マップ、コンペティティブ、不具合修正があれば分ける
- 重要な変更が少ない場合は「大きなバランス変更は少なめ」と書く
- 公式本文にない内容は絶対に追加しない
- 数値がある場合はできるだけそのまま残す
- Discordで読みやすいように箇条書きにする

出力形式：

【ざっくり要約】
・
・
・

【ランクに影響しそうな変更】
・

【確認しておくべきこと】
・

タイトル：
{title}

本文：
{article_text}
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
    )

    if not response.text:
        raise RuntimeError("Geminiから空の返答が返ってきました。")

    summary = response.text.strip()
    return summary[:3500]


def summarize_with_gemini_retry(title, article_text, max_retries=5):
    wait_seconds = 10

    for attempt in range(1, max_retries + 1):
        try:
            print(f"Gemini要約を実行します。試行 {attempt}/{max_retries}")
            return summarize_with_gemini_once(title, article_text)

        except Exception as e:
            error_text = str(e)
            print(f"Gemini要約に失敗しました。試行 {attempt}/{max_retries}")
            print(error_text)

            if attempt == max_retries:
                raise RuntimeError("Gemini要約が最大試行回数を超えて失敗しました。") from e

            print(f"{wait_seconds}秒待って再試行します。")
            time.sleep(wait_seconds)
            wait_seconds *= 2


def post_to_discord(title, url, summary):
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL が設定されていません。")

    content = "新しいVALORANTパッチノートが公開されました！"

    embed = {
        "title": title[:256],
        "url": url,
        "description": f"{summary}\n\n[公式ページを開く]({url})\n\n※AI要約のため、細かい数値や仕様は公式ページも確認してください。",
        "color": 16711680,
    }

    payload = {
        "username": "VALORANT Patch Bot",
        "content": content,
        "embeds": [embed],
    }

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)
    response.raise_for_status()


def main():
    latest_saved = load_latest_patch()
    latest_patch = find_latest_patch_note()

    title = latest_patch["title"]
    url = latest_patch["url"]

    print(f"公式サイト最新: {title}")
    print(f"URL: {url}")

    if latest_saved.get("url") == url:
        print("新しいパッチノートはありません。終了します。")
        return

    print("新しいパッチノートを検知しました。本文を取得します。")
    article_text = extract_article_text(url)

    print("Geminiで要約します。")
    summary = summarize_with_gemini_retry(title, article_text)

    print("Discordに投稿します。")
    post_to_discord(title, url, summary)

    print("投稿成功。latest_patch.jsonを更新します。")
    save_latest_patch(title, url)


if __name__ == "__main__":
    main()
