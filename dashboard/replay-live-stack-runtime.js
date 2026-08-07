(() => {
  let installed = false;

  function install() {
    if (installed) return;
    if (typeof api !== 'function') {
      setTimeout(install, 80);
      return;
    }

    installed = true;
    const baseApi = api;
    api = async function replayLiveStackAwareApi(path, options = {}) {
      if (path !== '/v1/replay/kronos' || !options?.body) {
        return baseApi(path, options);
      }

      let payload;
      try {
        payload = JSON.parse(options.body);
      } catch (_) {
        return baseApi(path, options);
      }

      const advanced = Boolean(document.getElementById('advancedForecast')?.checked);
      const configuredPaths = Number(document.getElementById('uncertaintyPaths')?.value);
      payload.advanced = advanced;
      if (advanced && Number.isFinite(configuredPaths)) {
        payload.paths = Math.max(3, Math.min(25, Math.round(configuredPaths)));
      } else {
        delete payload.paths;
      }

      return baseApi(path, {
        ...options,
        body: JSON.stringify(payload),
      });
    };
  }

  setTimeout(install, 0);
})();
