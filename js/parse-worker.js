// Parse a JSON file off the main thread. The page posts a URL.
self.onmessage = function (event) {
  var url = event.data;
  fetch(url).then(function (res) {
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }).then(function (data) {
    self.postMessage({ ok: true, data: data });
  }).catch(function (err) {
    self.postMessage({ ok: false, error: String(err && err.message || err) });
  });
};
