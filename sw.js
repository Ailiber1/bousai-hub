/* 防災ハブ Service Worker
 *
 * 目的: 一度開いておけば、通信がつながらない状況でも画面自体は表示できるようにする。
 *       （リンク先の情報を見るには通信が必要。この点はアプリ内にも明記している）
 *
 * 方針:
 *   - HTML（ナビゲーション）: ネットワーク優先 → 失敗したらキャッシュ
 *     ＝ 通信できる時は必ず最新版が出る。できない時だけ前回の画面を出す
 *   - それ以外の自サイト内ファイル: キャッシュ優先 → なければ取得して保存
 *   - 外部サイトへのリクエストは一切キャッシュしない（自オリジンのGETのみ扱う）
 */

var CACHE = 'bousai-hub-v1';
var SHELL = [
  './',
  './index.html',
  './manifest.json',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable-512.png',
  './icons/apple-touch-icon.png'
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE)
      // 1つでも取得に失敗すると全体が失敗するため、個別に追加して失敗は握る。
      // cache:'reload' でブラウザのHTTPキャッシュを迂回する
      // （これを付けないと、更新前の古いHTMLをそのままキャッシュしてしまうことがある）
      .then(function (cache) {
        return Promise.all(SHELL.map(function (url) {
          return cache.add(new Request(url, { cache: 'reload' })).catch(function () { return null; });
        }));
      })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.map(function (k) {
          return k === CACHE ? null : caches.delete(k);
        }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  var req = event.request;

  // 自オリジンのGET以外（外部サイト・POST等）は素通しする
  if (req.method !== 'GET') return;
  if (new URL(req.url).origin !== self.location.origin) return;

  // 画面遷移はネットワーク優先（更新を確実に届けるため）
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then(function (res) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put('./index.html', copy); });
          return res;
        })
        .catch(function () {
          return caches.match('./index.html').then(function (hit) {
            return hit || caches.match('./');
          });
        })
    );
    return;
  }

  // それ以外はキャッシュ優先
  event.respondWith(
    caches.match(req).then(function (hit) {
      if (hit) return hit;
      return fetch(req).then(function (res) {
        if (res && res.status === 200 && res.type === 'basic') {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
        }
        return res;
      });
    })
  );
});
