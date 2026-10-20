"use strict";

const tg = window.Telegram && window.Telegram.WebApp;
const NEW_MINUTES = 15; // an offer counts as "new" for this long after we first saw it
const REFRESH_MS = 60_000;

const state = {
  tab: "offers",
  me: null,
  filters: null,
  offers: null,
  error: null,
  sheet: null, // {filter|null}
  sort: "price", // price | rarity | new
  market: "all", // all | telegram | mrkt
  updatedAt: null,
  refreshing: false,
};

/* ── tiny DOM helpers (text is always set via textContent, never innerHTML) ── */

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === false || value == null) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "checked" || key === "disabled" || key === "value") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

function svg(paths, extra) {
  const ns = "http://www.w3.org/2000/svg";
  const el = document.createElementNS(ns, "svg");
  el.setAttribute("viewBox", "0 0 24 24");
  el.setAttribute("aria-hidden", "true");
  for (const d of paths) {
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", d);
    el.append(p);
  }
  if (extra) el.setAttribute("class", extra);
  return el;
}

const ICONS = {
  offers: ["M4 9h16v4H4z", "M5 13h14v7H5z", "M12 9v11", "M12 9c-2.5 0-4.5-1-4.5-2.6C7.5 5 9 4.3 10 5c1 .7 2 2.4 2 4z", "M12 9c2.5 0 4.5-1 4.5-2.6C16.5 5 15 4.3 14 5c-1 .7-2 2.4-2 4z"],
  filters: ["M4 7h9", "M17 7h3", "M4 17h3", "M11 17h9", "M13 4.5v5", "M7 14.5v5"],
  profile: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M4.5 20c.8-3.6 3.9-5.5 7.5-5.5s6.7 1.9 7.5 5.5"],
  refresh: ["M20 11a8 8 0 1 0-2.3 5.7", "M20 5v6h-6"],
  close: ["M6 6l12 12", "M18 6L6 18"],
};
const STAR = "M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6L12 17.3l-5.9 3.3 1.3-6.6L2.5 9.4l6.6-.8z";

function icon(name) {
  const el = svg(ICONS[name]);
  el.setAttribute("fill", "none");
  el.setAttribute("stroke", "currentColor");
  el.setAttribute("stroke-width", "1.8");
  el.setAttribute("stroke-linecap", "round");
  el.setAttribute("stroke-linejoin", "round");
  return el;
}

/* ── formatting ── */

const nf = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
const nfTon = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 4 });
const fmt = (n) => nf.format(n);
const pct = (permille) => (permille == null ? "" : fmt(permille / 10) + "%");

function priceNode(o) {
  if (o.currency === "TON") {
    return h("div", { class: "price ton" }, `${nfTon.format(o.price / 1e9)} TON`);
  }
  const star = svg([STAR]);
  return h("div", { class: "price" }, star, fmt(o.price));
}

function filterSummary(f) {
  const parts = [];
  if (f.collection) parts.push(f.collection);
  if (f.model) parts.push("модель " + f.model);
  if (f.backdrop) parts.push("фон " + f.backdrop);
  if (f.symbol) parts.push("символ " + f.symbol);
  if (f.min_price != null && f.max_price != null) parts.push(`цена ${fmt(f.min_price)}–${fmt(f.max_price)}`);
  else if (f.min_price != null) parts.push("цена от " + fmt(f.min_price));
  else if (f.max_price != null) parts.push("цена до " + fmt(f.max_price));
  if (f.min_ton != null && f.max_ton != null) parts.push(`цена ${nfTon.format(f.min_ton)}–${nfTon.format(f.max_ton)} TON`);
  else if (f.min_ton != null) parts.push(`цена от ${nfTon.format(f.min_ton)} TON`);
  else if (f.max_ton != null) parts.push(`цена до ${nfTon.format(f.max_ton)} TON`);
  if (f.max_rarity != null) parts.push("редкость до " + pct(f.max_rarity));
  return parts.length ? parts.join(", ") : "Любые подарки";
}

function minutesSince(iso) {
  if (!iso) return null;
  return Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000);
}

function timeAgo(iso) {
  if (!iso) return "ещё не было";
  const minutes = Math.round(minutesSince(iso));
  if (minutes < 1) return "только что";
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} ч назад` : new Date(iso).toLocaleDateString("ru-RU");
}

function agoMs(ms) {
  if (!ms) return "только что";
  return timeAgo(new Date(ms).toISOString());
}

function hexLuma(hex) {
  const n = parseInt(hex.slice(1), 16);
  return 0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
}

const safeColor = (c) => (typeof c === "string" && /^#[0-9a-f]{6}$/i.test(c) ? c : null);
const safeTgUrl = (u) => (typeof u === "string" && /^https:\/\/t\.me\/[\w./?=&%:-]+$/.test(u) ? u : null);
const safeImageUrl = (u) => (typeof u === "string" && /^https:\/\/nft\.fragment\.com\/gift\/[a-z0-9_-]+\.medium\.jpg$/.test(u) ? u : null);

/* ── Telegram glue ── */

function applyTheme() {
  if (tg && tg.colorScheme) document.documentElement.dataset.scheme = tg.colorScheme;
}
function haptic(kind) {
  try {
    if (kind === "ok") tg.HapticFeedback.notificationOccurred("success");
    else if (kind === "err") tg.HapticFeedback.notificationOccurred("error");
    else tg.HapticFeedback.impactOccurred("light");
  } catch (_) { /* not available outside Telegram */ }
}
function openLink(url) {
  const safe = safeTgUrl(url);
  if (!safe) return;
  if (tg && tg.openTelegramLink) tg.openTelegramLink(safe);
  else window.open(safe, "_blank", "noopener");
}
function ask(message) {
  return new Promise((resolve) => {
    if (tg && tg.showConfirm) tg.showConfirm(message, resolve);
    else resolve(window.confirm(message));
  });
}

/* ── API ── */

class ApiFail extends Error {}

async function api(path, { method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: { Authorization: "tma " + (tg ? tg.initData : ""), "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (_) {
    throw new ApiFail("Нет соединения с сервером. Проверьте интернет и повторите.");
  }
  let data = {};
  try { data = await response.json(); } catch (_) { /* empty body */ }
  if (!response.ok) throw new ApiFail(data.error || "Что-то пошло не так. Повторите позже.");
  return data;
}

async function loadAll() {
  state.error = null;
  state.me = null; // shows the skeleton
  render();
  try {
    const [me, filters, offers] = await Promise.all([api("/api/me"), api("/api/filters"), api("/api/offers")]);
    state.me = me;
    state.filters = filters.filters;
    state.offers = offers;
    state.updatedAt = Date.now();
  } catch (e) {
    state.error = e.message;
  }
  render();
}

const offersSignature = (o) => (o ? o.offers.map((x) => `${x.slug}:${x.price}`).join(",") + `|${o.total}` : "");

/** Re-read offers (and the match counters). `silent` = background refresh: no spinner, no error popups. */
async function refreshOffers(silent) {
  if (state.refreshing || !state.me) return;
  state.refreshing = true;
  if (!silent) render();
  try {
    const [me, filters, offers] = await Promise.all([api("/api/me"), api("/api/filters"), api("/api/offers")]);
    const changed = offersSignature(offers) !== offersSignature(state.offers);
    state.me = me;
    if (!state.sheet) state.filters = filters.filters;
    state.offers = offers;
    state.updatedAt = Date.now();
    state.refreshing = false;
    if (changed || !silent) render();
    else updateStatus();
  } catch (e) {
    state.refreshing = false;
    if (!silent) {
      render();
      if (tg && tg.showAlert) tg.showAlert(e.message);
    }
  }
}

async function reloadDerived() {
  try {
    const [me, filters, offers] = await Promise.all([api("/api/me"), api("/api/filters"), api("/api/offers")]);
    state.me = me;
    state.filters = filters.filters;
    state.offers = offers;
    state.updatedAt = Date.now();
  } catch (_) { /* the list itself is already updated; the next refresh will fix the rest */ }
}

/* ── offers: sorting and filtering happen in the browser ── */

const rarestOf = (o) => {
  const values = [o.model_rarity, o.backdrop_rarity, o.symbol_rarity].filter((v) => v != null);
  return values.length ? Math.min(...values) : Infinity;
};
const priceKey = (o) => [o.currency === "TON" ? 1 : 0, o.price];

const SORTS = {
  price: { label: "Цена", cmp: (a, b) => priceKey(a)[0] - priceKey(b)[0] || priceKey(a)[1] - priceKey(b)[1] },
  rarity: { label: "Редкость", cmp: (a, b) => rarestOf(a) - rarestOf(b) || SORTS.price.cmp(a, b) },
  new: { label: "Новизна", cmp: (a, b) => (b.seen_at || "").localeCompare(a.seen_at || "") },
};

const isNew = (o) => {
  const age = minutesSince(o.seen_at);
  return age != null && age <= NEW_MINUTES;
};

function visibleOffers() {
  const list = state.offers.offers.filter((o) => state.market === "all" || o.market === state.market);
  return [...list].sort(SORTS[state.sort].cmp);
}

/* ── views ── */

function demoNote() {
  return state.me && String(state.me.source).split(",").includes("mock")
    ? h("p", { class: "demo" }, "Демо-режим: показаны тестовые данные, а не настоящие лоты.")
    : null;
}

function emptyState(title, text, actionLabel, action) {
  return h("div", { class: "state" }, h("h2", null, title), h("p", null, text),
    actionLabel ? h("button", { class: "btn", onclick: action }, actionLabel) : null);
}

function skeletonView() {
  const card = () => h("div", { class: "offer skeleton", "aria-hidden": "true" },
    h("div", { class: "sk-hero" }), h("div", { class: "sk-line" }), h("div", { class: "sk-line short" }), h("div", { class: "sk-line" }));
  return [
    h("div", { class: "head" }, h("div", null, h("h1", null, "Лоты"), h("p", { class: "sk-text", "aria-label": "Загрузка" }, "Загружаю предложения"))),
    h("div", { class: "offers", role: "status", "aria-label": "Загрузка" }, card(), card(), card()),
  ];
}

function offerCard(o) {
  const title = h("div", { class: "caption" },
    h("div", { class: "title" }, o.collection),
    h("div", { class: "num" }, "№ " + o.number));
  const banner = h("div", { class: "banner" });
  const center = safeColor(o.backdrop_center);
  const edge = safeColor(o.backdrop_edge);
  if (center && edge) banner.style.background = `radial-gradient(120% 140% at 50% 10%, ${center}, ${edge})`;

  // The picture already contains the backdrop, the pattern and the gift itself. If it cannot be loaded
  // (unknown slug, CDN down) the card falls back to the compact colour banner.
  const imageUrl = safeImageUrl(o.image_url);
  if (imageUrl) {
    banner.classList.add("loading-image");
    const img = h("img", { class: "art", src: imageUrl, alt: `${o.collection} № ${o.number}`, loading: "lazy", decoding: "async" });
    img.addEventListener("load", () => { banner.classList.remove("loading-image"); banner.classList.add("has-image"); });
    img.addEventListener("error", () => { img.remove(); banner.classList.remove("loading-image"); });
    banner.append(img);
  }
  if (!imageUrl && center && edge && hexLuma(center) * 0.6 + hexLuma(edge) * 0.4 > 150) banner.classList.add("on-light");
  if (isNew(o)) banner.append(h("span", { class: "badge-new" }, "Новый"));
  banner.append(title);

  const traits = h("dl", { class: "traits" });
  for (const [label, value, rarity] of [
    ["Модель", o.model, o.model_rarity],
    ["Фон", o.backdrop, o.backdrop_rarity],
    ["Символ", o.symbol, o.symbol_rarity],
  ]) {
    if (!value) continue;
    const name = h("dd", null, value);
    if (label === "Фон" && center && edge) {
      const dot = h("i", { class: "swatch", "aria-hidden": "true" });
      dot.style.background = `radial-gradient(circle at 35% 30%, ${center}, ${edge})`;
      name.prepend(dot);
    }
    const rare = rarity != null && rarity <= 10; // 1% or rarer
    traits.append(h("dt", null, label), name, h("dd", { class: "rar" + (rare ? " rare" : ""), title: rare ? "Редкий признак" : null }, pct(rarity)));
  }

  const ownerUrl = safeTgUrl(o.owner_url);
  const owner = h("div", { class: "owner" }, "Владелец: ",
    ownerUrl ? h("a", { href: ownerUrl, onclick: (e) => { e.preventDefault(); openLink(ownerUrl); } }, "@" + o.owner_username)
      : o.owner_name ? `${o.owner_name} (профиль скрыт)` : "скрыт");

  const marketUrl = safeTgUrl(o.market_url);
  const actions = h("div", { class: "actions-row" },
    marketUrl ? h("button", { class: "btn", onclick: () => { haptic(); openLink(marketUrl); } }, `Купить на ${o.market_title}`) : null,
    h("button", { class: marketUrl ? "btn quiet" : "btn", onclick: () => { haptic(); openLink(o.url); } }, "Страница подарка"));
  return h("article", { class: "offer" }, banner, traits,
    h("div", { class: "buy" }, priceNode(o), marketUrl ? h("span", { class: "market" }, o.market_title) : null),
    actions, owner);
}

function statusText() {
  const total = state.offers ? state.offers.total : 0;
  const found = state.offers && state.offers.has_filters ? `Найдено ${total}.` : "";
  return `${found} Обновлено ${agoMs(state.updatedAt)}.`.trim();
}
function updateStatus() {
  const el = document.getElementById("status");
  if (el) el.textContent = statusText();
}

function segmented(label, options, current, onPick) {
  return h("div", { class: "seg", role: "group", "aria-label": label },
    options.map(([value, text]) => h("button", { type: "button", class: "seg-btn", "aria-pressed": String(current === value),
      onclick: () => { if (current !== value) { haptic(); onPick(value); } } }, text)));
}

function offersView() {
  const o = state.offers;
  const head = h("div", { class: "head" },
    h("div", null, h("h1", null, "Лоты"), h("p", { id: "status" }, statusText())),
    h("button", { class: "icon-btn" + (state.refreshing ? " spinning" : ""), "aria-label": "Обновить", disabled: state.refreshing,
      onclick: () => { haptic(); refreshOffers(false); } }, icon("refresh")));

  if (!o.has_filters) {
    return [head, demoNote(), emptyState("Пока нет активных фильтров", "Создайте фильтр, и здесь появятся подходящие предложения.",
      "Создать фильтр", () => { state.tab = "filters"; openSheet(null); })];
  }
  if (!o.offers.length) {
    return [head, demoNote(), emptyState("Ничего не найдено", `Я показываю предложения, замеченные за последние ${state.me.fresh_minutes} мин. Новые подходящие лоты придут уведомлением в чат с ботом, если оно включено.`)];
  }

  const markets = [...new Set(o.offers.map((x) => x.market))];
  const count = (m) => o.offers.filter((x) => m === "all" || x.market === m).length;
  const titles = Object.fromEntries(o.offers.map((x) => [x.market, x.market_title]));
  const toolbar = h("div", { class: "toolbar" },
    segmented("Сортировка", Object.entries(SORTS).map(([k, v]) => [k, v.label]), state.sort, (v) => { state.sort = v; render(); }),
    markets.length > 1
      ? h("div", { class: "chips", role: "group", "aria-label": "Площадка" },
        [["all", "Все"], ...markets.map((m) => [m, titles[m]])].map(([m, text]) =>
          h("button", { type: "button", class: "chip", "aria-pressed": String(state.market === m),
            onclick: () => { haptic(); state.market = m; render(); } }, `${text} ${count(m)}`)))
      : null);

  const list = visibleOffers();
  return [head, demoNote(), toolbar,
    list.length ? h("div", { class: "offers" }, list.map(offerCard)) : emptyState("На этой площадке пока пусто", "Выберите «Все» или другую площадку.")];
}

function filtersView() {
  const head = h("div", { class: "head" },
    h("div", null, h("h1", null, "Фильтры"), h("p", null, "Лот подходит, если выполнены все условия одного включённого фильтра.")),
    h("button", { class: "btn", onclick: () => openSheet(null) }, "Новый"));

  const body = state.filters.length
    ? h("div", { class: "list" }, state.filters.map((f) => filterRow(f)))
    : emptyState("Фильтров пока нет", "Задайте коллекцию, модель, фон или цену, и бот будет искать подходящие лоты.",
      "Создать фильтр", () => openSheet(null));
  return [head, demoNote(), body];
}

function filterRow(f) {
  const toggle = h("label", { class: "switch" },
    h("input", { type: "checkbox", role: "switch", checked: f.is_active, "aria-label": `Фильтр «${f.name}» включён`,
      onchange: (e) => toggleFilter(f, e.target.checked, e.target) }),
    h("i"));
  const pill = f.is_active && f.matches != null
    ? h("span", { class: "pill" + (f.matches ? " live" : ""), title: "Лотов сейчас подходит" }, f.matches ? `${f.matches} сейчас` : "нет лотов")
    : null;
  return h("div", { class: "row" + (f.is_active ? "" : " off") },
    h("button", { class: "main", type: "button", onclick: () => openSheet(f) },
      h("span", { class: "name" }, f.name), h("span", { class: "sub" }, filterSummary(f))),
    pill, toggle);
}

function profileView() {
  const m = state.me;
  const s = m.stats;
  const name = m.user.first_name || "коллекционер";
  const head = h("div", { class: "head" }, h("div", null, h("h1", null, "Профиль"), h("p", null, "Привет, " + name + "!")));

  const notify = h("div", { class: "panel" }, h("div", { class: "setting" },
    h("div", { class: "main" }, h("div", null, "Уведомления в чат"),
      h("div", { class: "sub" }, "Присылать новые подходящие лоты в чат с ботом.")),
    h("label", { class: "switch" },
      h("input", { type: "checkbox", role: "switch", checked: m.notifications_enabled, "aria-label": "Уведомления в чат",
        onchange: (e) => toggleNotifications(e.target.checked, e.target) }), h("i"))));

  const facts = (rows) => h("div", { class: "panel" }, h("dl", { class: "facts" }, rows.flatMap(([k, v]) => [h("dt", null, k), h("dd", null, String(v))])));
  return [head, demoNote(), notify,
    facts([["Ваши фильтры", `${s.filters_total} (включено ${s.filters_active})`], ["Получено уведомлений", s.notifications_received]]),
    facts([["Последний опрос рынка", timeAgo(s.last_poll_at)], ["Известных предложений", s.listings_known], ["Пользователей", s.users]]),
    h("p", { class: "fine" }, "Бот проверяет рынок по расписанию, поэтому уведомления приходят с задержкой. Сделки проходят в Telegram по ссылке на подарок, бот их не проводит.")];
}

function sheetView() {
  const sheet = state.sheet;
  const f = sheet.filter;
  const val = (key) => (f && f[key] != null ? String(f[key]) : "");
  const input = (id, label, value, attrs) =>
    h("div", { class: "field" }, h("label", { for: id }, label), h("input", { id, name: id, value, autocomplete: "off", ...attrs }));

  const form = h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); saveFilter(e.target); } },
    input("name", "Название", f ? f.name : "", { maxlength: "64", placeholder: "Например, Дешёвый Pepe" }),
    input("collection", "Коллекция", val("collection"), { placeholder: "Любая, например Plush Pepe" }),
    input("model", "Модель", val("model"), { placeholder: "Любая" }),
    h("div", { class: "pair" },
      input("backdrop", "Фон", val("backdrop"), { placeholder: "Любой" }),
      input("symbol", "Символ", val("symbol"), { placeholder: "Любой" })),
    h("div", { class: "pair" },
      input("min_price", "Цена от, Stars", val("min_price"), { inputmode: "numeric", placeholder: "Без минимума" }),
      input("max_price", "Цена до, Stars", val("max_price"), { inputmode: "numeric", placeholder: "Без максимума" })),
    h("div", { class: "pair" },
      input("min_ton", "Цена от, TON", val("min_ton"), { inputmode: "decimal", placeholder: "Без минимума" }),
      input("max_ton", "Цена до, TON", val("max_ton"), { inputmode: "decimal", placeholder: "Без максимума" })),
    input("max_rarity", "Редкость не больше, %", f && f.max_rarity != null ? String(f.max_rarity / 10) : "",
      { inputmode: "decimal", placeholder: "Любая, например 2" }),
    h("div", { class: "actions" },
      h("button", { class: "btn wide", type: "submit" }, "Сохранить"),
      f ? h("button", { class: "btn danger wide", type: "button", onclick: () => removeFilter(f) }, "Удалить фильтр") : null));

  return h("div", { class: "scrim", onclick: (e) => { if (e.target === e.currentTarget) closeSheet(); } },
    h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": f ? "Редактирование фильтра" : "Новый фильтр" },
      h("div", { class: "top" }, h("h2", null, f ? "Фильтр" : "Новый фильтр"),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Закрыть", onclick: closeSheet }, icon("close"))),
      form));
}

function tabbar() {
  const total = state.offers && state.offers.has_filters ? state.offers.total : 0;
  const tab = (id, label) => h("button", { class: "tab", role: "tab", "aria-selected": String(state.tab === id),
    onclick: () => { if (state.tab !== id) { haptic(); state.tab = id; render(); window.scrollTo(0, 0); } } },
    h("span", { class: "tab-icon" }, icon(id), id === "offers" && total ? h("b", { class: "tab-badge" }, total > 99 ? "99+" : total) : null), label);
  return h("nav", { class: "tabbar", role: "tablist" }, h("div", { class: "inner" },
    tab("offers", "Лоты"), tab("filters", "Фильтры"), tab("profile", "Профиль")));
}

function render() {
  const root = document.getElementById("app");
  const page = h("main", { class: "page" });

  if (!tg || !tg.initData) {
    page.append(emptyState("Откройте через Telegram", "Это мини-приложение работает внутри Telegram: запустите его из чата с ботом."));
  } else if (state.error) {
    page.append(emptyState("Не удалось загрузить данные", state.error, "Повторить", loadAll));
  } else if (!state.me) {
    page.append(...skeletonView());
  } else {
    const views = { offers: offersView, filters: filtersView, profile: profileView };
    page.append(...views[state.tab]().filter(Boolean));
  }

  root.replaceChildren(h("div", { class: "shell" }, page, state.me ? tabbar() : null, state.sheet ? sheetView() : null));
}

/* ── actions ── */

// The sheet is updated in place (not re-rendered) so a failed save never wipes what was typed.
function sheetBusy(busy) {
  for (const button of document.querySelectorAll(".sheet .actions button")) button.disabled = busy;
}
function sheetError(message) {
  const form = document.querySelector(".sheet .form");
  if (!form) return;
  const old = form.querySelector(".err");
  if (old) old.remove();
  form.insertBefore(h("p", { class: "err", role: "alert" }, message), form.querySelector(".actions"));
}

function openSheet(filter) {
  state.sheet = { filter };
  render();
  const first = document.getElementById(filter ? "collection" : "name");
  if (first) first.focus();
}
function closeSheet() {
  state.sheet = null;
  render();
}

function readForm(form) {
  const text = (id) => form.elements[id].value.trim() || null;
  const rarity = form.elements.max_rarity.value.trim().replace(",", ".");
  let permille = null; // the API stores rarity in per-mille; people think in percent
  if (rarity) {
    const value = Number(rarity);
    if (!Number.isFinite(value) || value < 0.1 || value > 100) {
      throw new ApiFail("Редкость: число процентов от 0,1 до 100, например 2 или 0,5.");
    }
    permille = Math.round(value * 10);
  }
  const body = {
    collection: text("collection"), model: text("model"), backdrop: text("backdrop"), symbol: text("symbol"),
    min_price: text("min_price"), max_price: text("max_price"),
    min_ton: text("min_ton"), max_ton: text("max_ton"), max_rarity: permille,
  };
  const name = text("name");
  if (name) body.name = name;
  return body;
}

async function saveFilter(form) {
  const sheet = state.sheet;
  sheetBusy(true);
  try {
    const body = readForm(form);
    const result = sheet.filter
      ? await api("/api/filters/" + sheet.filter.id, { method: "PATCH", body })
      : await api("/api/filters", { method: "POST", body });
    const list = state.filters.filter((f) => f.id !== result.filter.id);
    state.filters = [...list, result.filter].sort((a, b) => a.id - b.id);
    state.sheet = null;
    haptic("ok");
    render();
    reloadDerived().then(render);
  } catch (e) {
    sheetBusy(false);
    sheetError(e.message);
    haptic("err");
  }
}

async function removeFilter(f) {
  if (!(await ask(`Удалить фильтр «${f.name}»?`))) return;
  sheetBusy(true);
  try {
    await api("/api/filters/" + f.id, { method: "DELETE" });
    state.filters = state.filters.filter((x) => x.id !== f.id);
    state.sheet = null;
    haptic("ok");
    render();
    reloadDerived().then(render);
  } catch (e) {
    sheetBusy(false);
    sheetError(e.message);
  }
}

async function toggleFilter(f, active, checkbox) {
  try {
    const result = await api("/api/filters/" + f.id, { method: "PATCH", body: { is_active: active } });
    Object.assign(f, result.filter);
    haptic();
    render();
    reloadDerived().then(render);
  } catch (e) {
    checkbox.checked = !active;
    if (tg && tg.showAlert) tg.showAlert(e.message);
  }
}

async function toggleNotifications(enabled, checkbox) {
  try {
    await api("/api/notifications", { method: "POST", body: { enabled } });
    state.me.notifications_enabled = enabled;
    haptic();
  } catch (e) {
    checkbox.checked = !enabled;
    if (tg && tg.showAlert) tg.showAlert(e.message);
  }
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.sheet) closeSheet(); });

// Keep the list fresh while the app is open: refresh once a minute, and keep "updated N min ago" honest.
setInterval(() => {
  if (document.visibilityState !== "visible" || !state.me || state.sheet) return;
  if (Date.now() - (state.updatedAt || 0) >= REFRESH_MS) refreshOffers(true);
  else if (state.tab === "offers") updateStatus();
}, 15_000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.me && Date.now() - (state.updatedAt || 0) >= REFRESH_MS) refreshOffers(true);
});

if (tg) {
  tg.ready();
  tg.expand();
  applyTheme();
  tg.onEvent("themeChanged", applyTheme);
}
if (tg && tg.initData) loadAll();
else render();
