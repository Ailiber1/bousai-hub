#!/usr/bin/env python3
"""防災ハブ リンク点検スクリプト

このアプリの価値は「一次情報への最短距離」であることに尽きる。
リンクが死ねば価値はゼロになるので、定期的に全リンクの生存を確認する。

【確認すること】
  1. HTTPステータス           … 404・DNS消滅などの「分かりやすい死に方」
  2. ページタイトルの照合     … 200を返すが中身が別物になっている場合
     （実例: nagano-bousai.jp は正常に開くが中身は長野「市」だった。
       bousai.okinawa.jp は第三者が取得して詐欺サイトになっていた）

【方針】
  - 外部パッケージを一切使わない（標準ライブラリのみ）。
    pip install はサプライチェーン攻撃の入口になるため。
  - 1サーバーにつき1リクエストしか送らない。
    自治体サイトは1,900以上あるが、それぞれ別のサーバーなので負荷は分散する。
  - 同一サーバーに大量に投げる相手（Yahoo!等）は代表サンプルのみにする。
    実際に並列アクセスして429を返された経緯がある。

使い方:
    python3 tools/check-links.py            # 全件点検
    python3 tools/check-links.py --quick    # 都道府県レベルのみ（動作確認用）
"""

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
TIMEOUT = 25

# 自治体サイトには証明書の構成が古いものがあり、
# 証明書検証で落とすと「生きているのに死んだ」と誤判定する。
# ここで見たいのは「そのページが存在するか」なので検証を緩める。
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# Yahoo!のように同一サーバーへ集中するものは、全件ではなく代表のみ確認する
SAMPLE_ONLY_HOSTS = ('crisis.yahoo.co.jp',)


# 「リンクが死んだ」とは断定できないステータス。
#  403 … サイトが自動アクセスや海外からのアクセスを拒否している場合が多い。
#        GitHub Actionsの実行サーバーは海外にあるため、日本の電力会社や自治体が
#        正常稼働していても403を返すことが実際にあった。
#  5xx … 一時的な不調。次回の点検で回復していることが多い。
AMBIGUOUS_STATUS = ('401', '403', '429', '500', '502', '503', '504')


def fetch(url):
    """(ステータス文字列, ページタイトル) を返す"""
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
        'Accept-Language': 'ja,en;q=0.9',
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as res:
            raw = res.read(200000)
            enc = 'utf-8'
            m = re.search(r'charset=([\w-]+)', res.headers.get('Content-Type', ''), re.I)
            if m:
                enc = m.group(1)
            html = raw.decode(enc, 'ignore')
            # 日本語の自治体サイトはShift_JIS等が残っていることがある
            m2 = re.search(r'charset=["\']?([\w-]+)', html[:2000], re.I)
            if m2 and m2.group(1).lower() != enc.lower():
                try:
                    html = raw.decode(m2.group(1), 'ignore')
                except LookupError:
                    pass
            t = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
            title = re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', '', t.group(1))).strip()[:80] if t else ''
            return str(res.status), title
    except urllib.error.HTTPError as e:
        return str(e.code), ''
    except Exception as e:
        return 'ERR:' + type(e).__name__, ''


def load_cities():
    """cities.js から [(市区町村名, よみ, URL)] を読み出す"""
    src = open(os.path.join(ROOT, 'cities.js'), encoding='utf-8').read()
    body = src[src.index('{'):src.rindex('}') + 1]
    data = json.loads(body)
    out = []
    for _pref_code, rows in data.items():
        for _jis, name, yomi, url in rows:
            if url:
                out.append((name, yomi, url))
    return out


def name_tokens(name, yomi):
    """タイトル照合に使う手がかりを作る。

    自治体サイトのタイトルは表記がまちまちなので、単純な部分一致では誤検知する。
      「さいたま市西区」→ タイトルは「さいたま市／西区」（間に記号が入る）
      「仙台市青葉区」  → タイトルは「青葉区トップページ｜仙台市」（順序が逆）
      「剣淵町」        → タイトルは「絵本の里けんぶち町」（ひらがな表記）
      「南牧村」        → タイトルは「群馬県なんもく村」（ひらがな表記）
    そこで、漢字・ひらがな・政令市の分割など複数の手がかりを用意し、
    どれか1つでも当たれば正常とみなす。
    """
    toks = set()
    if name:
        toks.add(name)
        toks.add(re.sub(r'[市区町村]$', '', name))
        # 政令指定都市の区は「市の部分」と「区の部分」に割る
        m = re.match(r'^(.+?市)(.+区)$', name)
        if m:
            toks.add(m.group(1))
            toks.add(m.group(2))
            toks.add(re.sub(r'市$', '', m.group(1)))
            toks.add(re.sub(r'区$', '', m.group(2)))
    if yomi:
        toks.add(yomi)
        toks.add(re.sub(r'(ちょう|まち|むら|そん|し|く)$', '', yomi))
    return {t for t in toks if len(t) >= 2}


def load_app_links():
    """index.html に直接書かれている固定URL（県公式・防災ポータル・電力会社など）

    注意: ソース内には
        'https://teideninfo.tepco.co.jp/flash/' + pref[3] + '000000000.html'
    のように、変数と連結して完成させるURLがある。
    この「連結の途中」を単独のURLとして点検すると、実在しないURLを叩いて
    誤検知になる。閉じクォートの直後が `+` のものは除外する。
    """
    src = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
    cleaned = set()
    # クォートで囲まれたURLを、直後に連結演算子が続くかどうかも含めて拾う
    for m in re.finditer(r"['\"](https://[a-zA-Z0-9./_%#?=&@:-]+)['\"](\s*\+)?", src):
        url, concat = m.group(1), m.group(2)
        if concat:
            continue                      # 連結の途中なので単独では実在しない
        if 'w3.org' in url or 'openxmlformats' in url:
            continue
        if any(h in url for h in SAMPLE_ONLY_HOSTS):
            continue                      # 同一サーバー集中を避け、別途サンプルで確認する
        cleaned.add(url)
    return sorted(cleaned)


# lg.jp と地域型JPドメイン(city./town./vill.)は地方公共団体しか取得できない。
# したがって第三者に乗っ取られる心配がなく、200が返れば正常とみなしてよい。
# タイトル照合が必要なのは、小規模自治体が使っている .com/.net/.org などの一般ドメイン。
GOV_DOMAIN = re.compile(r'\.lg\.jp$|^(www\d*\.)?(city|town|vill)\.[^.]+\.[^.]+\.jp$')


def is_gov_domain(url):
    try:
        host = re.sub(r'^https?://', '', url).split('/')[0].lower()
    except Exception:
        return False
    return bool(GOV_DOMAIN.search(host))


def check_city(item):
    """戻り値の severity: 'error'=要対応 / 'warn'=要確認 / None=正常"""
    name, yomi, url = item
    status, title = fetch(url)

    if status.startswith('ERR'):
        # このスクリプトからだけ失敗する自治体サイトが実在する（TLS構成や文字コードの都合）。
        # 実ブラウザでは開けることが多いので、断定せず「要確認」に留める。
        return (name, url, status, title, 'warn', 'スクリプトから接続できず（ブラウザで要確認）')
    if status in AMBIGUOUS_STATUS:
        return (name, url, status, title, 'warn',
                '自動アクセスまたは海外からのアクセスを拒否している可能性（ブラウザで要確認）')
    if not status.startswith('2'):
        return (name, url, status, title, 'error', 'アクセスできない')

    # 一般ドメインのみ、中身が別物になっていないかをタイトルで確認する
    if not is_gov_domain(url) and title and not any(t in title for t in name_tokens(name, yomi)):
        return (name, url, status, title, 'warn',
                f'一般ドメインでタイトルに自治体名が見当たらない: 「{title}」')
    return (name, url, status, title, None, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true', help='都道府県レベルのみ点検')
    args = ap.parse_args()

    errors = []   # 要対応: 明確に到達できない
    warns = []    # 要確認: 断定できないが目視すべき
    checked = 0

    # --- 1. アプリに直接書かれた固定リンク（県公式・防災ポータル・電力会社・全国共通） ---
    app_links = load_app_links()
    print(f'[1/3] 固定リンク {len(app_links)} 件を点検中...', file=sys.stderr)
    with ThreadPoolExecutor(max_workers=8) as ex:
        for url, (status, title) in zip(app_links, ex.map(fetch, app_links)):
            checked += 1
            if status.startswith('ERR') or status in AMBIGUOUS_STATUS:
                warns.append(f'- [固定リンク] {url} → {status}（ブラウザで要確認）')
            elif not status.startswith('2'):
                errors.append(f'- [固定リンク] {url} → **{status}**')

    # --- 2. Yahoo!天気・災害は同一サーバーなので代表サンプルのみ ---
    samples = ['01/01101', '13/13101', '27/27102', '43/43211', '47/47201']
    print(f'[2/3] Yahoo!天気・災害を代表 {len(samples)} 件で点検中...', file=sys.stderr)
    for s in samples:
        u = f'https://crisis.yahoo.co.jp/evacuation/{s}/'
        status, _ = fetch(u)
        checked += 1
        if not status.startswith('2'):
            errors.append(f'- [避難情報] {u} → **{status}**'
                          '（URL形式が変わると全1,918市区町村の避難情報が失われます。最優先で確認）')

    # --- 3. 市区町村の公式サイト ---
    if not args.quick:
        cities = load_cities()
        print(f'[3/3] 市区町村 {len(cities)} 件を点検中...', file=sys.stderr)
        with ThreadPoolExecutor(max_workers=16) as ex:
            for name, url, status, title, sev, msg in ex.map(check_city, cities):
                checked += 1
                if sev == 'error':
                    errors.append(f'- [{name}] {url} → **{status}**')
                elif sev == 'warn':
                    warns.append(f'- [{name}] {url} → {status} / {msg}')

    print(f'\n点検完了。{checked} 件を確認 / 要対応 {len(errors)} 件 / 要確認 {len(warns)} 件',
          file=sys.stderr)

    if errors or warns:
        print('## 防災ハブ リンク点検の結果\n')
        print(f'{checked} 件を確認しました。\n')
        if errors:
            print(f'### 要対応（{len(errors)} 件）\n')
            print('リンク先に到達できませんでした。修正が必要です。\n')
            for e in errors:
                print(e)
            print()
        if warns:
            print(f'### 要確認（{len(warns)} 件）\n')
            print('自動判定では断定できません。ブラウザで開いて目視確認してください。\n')
            for w in warns:
                print(w)
            print()
        print('---')
        print('- 一部の自治体サイトは、このスクリプトからのアクセスだけ失敗することがあります'
              '（TLS構成や文字コードの都合）。実ブラウザでは正常なことが多いです。')
        print('- **GitHub Actionsの実行サーバーは海外にあります。** 日本の電力会社や自治体には'
              '海外からのアクセスを拒否する設定のところがあり、正常稼働していても403が返ります。'
              '「要確認」に403が出た場合は、まず日本国内のブラウザで開いて確認してください。')
        print('- 「一般ドメインでタイトルに自治体名が見当たらない」は、ドメインが別の主体に'
              '渡っている可能性を示します。過去に自治体名を含むドメインが詐欺サイトになっていた例があるため、'
              '必ず目視で確認してください。')
        print('- 修正手順は `docs/spec.md` の 6-2 / 6-3 を参照してください。')

    # 要対応があるときだけ失敗扱いにする（要確認だけなら通知しない）
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
