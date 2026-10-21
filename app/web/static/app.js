"use strict";

const tg = window.Telegram && window.Telegram.WebApp;
const NEW_MINUTES = 15; // an offer counts as "new" for this long after we first saw it
const NEW_HOURS = 6; // the "Новые" view keeps an offer this long after we first saw it
const REFRESH_MS = 60_000;

function loadView() {
  try { return localStorage.getItem("view") === "all" ? "all" : "new"; } catch (_) { return "new"; }
}

const state = {
  tab: "offers",
  me: null,
  filters: null,
  offers: null,
  error: null,
  sheet: null, // filter editor: {filter: existing filter | null, draft: prefilled values for a new one | null}
  detail: null, // the offer whose card is open
  query: "", // search in "Лоты"
  view: loadView(), // new (only gifts that appeared since tracking began) | all
  sort: "new", // price | rarity | new
  market: "all", // all | telegram | mrkt
  updatedAt: null,
  refreshing: false,
  refreshError: null, // the last refresh failed: the list is stale but still shown
};
const SEARCH_FROM = 7; // show the search box once there are this many offers

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
  plus: ["M12 5v14", "M5 12h14"],
  radar: ["M20.5 12A8.5 8.5 0 1 1 12 3.5", "M16 12a4 4 0 1 1-4-4", "M12 12l6-6", "M19 4.5v.01"],
  alert: ["M12 8.5v4.5", "M12 16.5v.01", "M10.3 4.2L2.9 17.3a2 2 0 0 0 1.7 3h14.8a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z"],
  bell: ["M6.5 16.5V11a5.5 5.5 0 1 1 11 0v5.5l1.5 2H5z", "M10 20.5a2 2 0 0 0 4 0"],
  search: ["M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z", "M16.2 16.2L20 20"],
  chevron: ["M9.5 6l6 6-6 6"],
  cart: ["M3.5 4.5h2.2l2.1 10.2h10.4l1.9-7.2H6.8", "M9.5 19.5h.01", "M17 19.5h.01"],
  link: ["M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1", "M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"],
};
const STAR = "M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6L12 17.3l-5.9 3.3 1.3-6.6L2.5 9.4l6.6-.8z";
const GEM = "M7.2 4h9.6l4.2 5.2L12 20.5 3 9.2z"; // a generic cut gem for TON prices (not the TON logo)

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

const unpriced = (o) => o.currency === "NONE"; // seen on the market's account, price not published

function priceNode(o) {
  if (unpriced(o)) return h("span", { class: "price unpriced" }, h("span", { class: "unit" }, "Цена на " + o.market_title));
  const ton = o.currency === "TON";
  return h("span", { class: "price" + (ton ? " ton" : " stars") },
    svg([ton ? GEM : STAR], "glyph"),
    h("span", { class: "amount" }, ton ? nfTon.format(o.price / 1e9) : fmt(o.price)),
    ton ? h("span", { class: "unit" }, "TON") : h("span", { class: "sr-only" }, " Stars"));
}

// Rarity is per-mille. The tiers only colour the chip; the number itself is always shown.
function rarityTier(permille) {
  if (permille == null) return null;
  if (permille <= 10) return "legendary";
  if (permille <= 30) return "epic";
  if (permille <= 100) return "rare";
  return "common";
}
const TIER_TITLES = {
  legendary: "Очень редкий признак: 1% и меньше",
  epic: "Редкий признак: до 3%",
  rare: "Необычный признак: до 10%",
};

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
    else if (kind === "select") tg.HapticFeedback.selectionChanged();
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
    state.refreshError = null;
    openLinkedOffer();
  } catch (e) {
    state.error = e.message;
  }
  render();
}

// A notification's "open in the app" button links here with ?offer=<slug>: show that offer's card once.
let linkedOfferHandled = false;
function openLinkedOffer() {
  if (linkedOfferHandled) return;
  linkedOfferHandled = true;
  let slug = null;
  try { slug = new URLSearchParams(window.location.search).get("offer"); } catch (_) { /* no query string */ }
  const hit = slug && state.offers.offers.find((x) => x.slug === slug);
  if (hit) state.detail = hit;
}

const offersSignature = (o) => (o ? o.offers.map((x) => `${x.slug}:${x.price}`).join(",") + `|${o.total}` : "");

/** Re-read offers (and the match counters). `silent` = background refresh: no spinner. A failure keeps the
 *  old list on screen and shows a notice with a retry button instead of a popup. */
async function refreshOffers(silent) {
  if (state.refreshing || !state.me) return;
  state.refreshing = true;
  if (!silent) render();
  try {
    const [me, filters, offers] = await Promise.all([api("/api/me"), api("/api/filters"), api("/api/offers")]);
    const changed = offersSignature(offers) !== offersSignature(state.offers) || state.refreshError;
    state.me = me;
    if (!state.sheet) state.filters = filters.filters;
    state.offers = offers;
    state.updatedAt = Date.now();
    state.refreshing = false;
    state.refreshError = null;
    if (changed || !silent) render();
    else updateStatus();
  } catch (e) {
    state.refreshing = false;
    const first = !state.refreshError;
    state.refreshError = e.message;
    if (!silent || first) render();
    if (!silent) haptic("err");
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

/* ── offers: sorting, filtering and search happen in the browser ── */

const TRAITS = [["Модель", "model"], ["Фон", "backdrop"], ["Символ", "symbol"]];

const rarestOf = (o) => {
  const values = [o.model_rarity, o.backdrop_rarity, o.symbol_rarity].filter((v) => v != null);
  return values.length ? Math.min(...values) : Infinity;
};
const priceKey = (o) => [unpriced(o) ? 2 : o.currency === "TON" ? 1 : 0, o.price];

const SORTS = {
  price: { label: "Цена", cmp: (a, b) => priceKey(a)[0] - priceKey(b)[0] || priceKey(a)[1] - priceKey(b)[1] },
  rarity: { label: "Редкость", cmp: (a, b) => rarestOf(a) - rarestOf(b) || SORTS.price.cmp(a, b) },
  new: { label: "Новизна", cmp: (a, b) => (b.seen_at || "").localeCompare(a.seen_at || "") },
};

const isNew = (o) => {
  const age = minutesSince(o.seen_at);
  return age != null && age <= NEW_MINUTES;
};

// "new" = discovered after the first pass over its collection (the pre-existing inventory is never new)
const isFresh = (o) => {
  if (!o.is_new) return false;
  const age = minutesSince(o.seen_at);
  return age != null && age <= NEW_HOURS * 60;
};

const fold = (text) => String(text || "").toLocaleLowerCase("ru-RU").replace(/ё/g, "е");

// Every word of the query must occur in the collection, model, backdrop, symbol, market or number.
function matchesQuery(o, query) {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = fold([o.collection, o.model, o.backdrop, o.symbol, o.market_title, "№" + o.number, o.number].join(" "));
  return words.every((w) => hay.includes(w.replace(/^#/, "")));
}

function visibleOffers() {
  const list = state.offers.offers.filter((o) =>
    (state.market === "all" || o.market === state.market) && (state.view === "all" || isFresh(o)) && matchesQuery(o, state.query));
  return [...list].sort(SORTS[state.sort].cmp);
}

function setView(view) {
  state.view = view;
  try { localStorage.setItem("view", view); } catch (_) { /* private mode: the choice just isn't remembered */ }
  render();
}

/* ── views ── */

function demoNote() {
  return state.me && String(state.me.source).split(",").includes("mock")
    ? h("p", { class: "demo" }, "Демо-режим: показаны тестовые данные, а не настоящие лоты.")
    : null;
}

function emptyState(iconName, title, text, actionLabel, action) {
  return h("div", { class: "state" },
    h("div", { class: "state-icon" }, icon(iconName)),
    h("h2", null, title), h("p", null, text),
    actionLabel ? h("button", { class: "btn", onclick: action }, actionLabel) : null);
}

// First run: ready-made starting points. Each only pre-fills the form, the user checks the numbers and saves.
const PRESETS = [
  { title: "Недорогие за Stars", sub: "Любая коллекция, до 2 000 Stars", draft: { name: "Недорогие за Stars", max_price: 2000 } },
  { title: "Недорогие за TON", sub: "Любая коллекция, до 10 TON (MRKT)", draft: { name: "Недорогие за TON", max_ton: 10 } },
  { title: "Редкие признаки", sub: "Модель, фон или символ у 1% подарков и меньше", draft: { name: "Редкие признаки", max_rarity: 10 } },
  { title: "Свой фильтр", sub: "Коллекция, модель, фон, символ, цена", draft: null },
];

function onboarding() {
  return h("section", { class: "onboarding" },
    h("div", { class: "state compact" },
      h("div", { class: "state-icon" }, icon("radar")),
      h("h2", null, "С чего начнём?"),
      h("p", null, "Выберите заготовку: я подставлю условия в фильтр, а вы проверите их и сохраните. Подходящие лоты появятся во вкладке «Лоты» и придут в чат.")),
    h("div", { class: "presets" }, PRESETS.map((p) =>
      h("button", { type: "button", class: "preset", onclick: () => { haptic(); state.tab = "filters"; render(); openSheet(null, p.draft); } },
        h("span", { class: "preset-text" }, h("span", { class: "name" }, p.title), h("span", { class: "sub" }, p.sub)),
        icon("chevron")))));
}

function skeletonView() {
  const card = () => h("div", { class: "offer skeleton", "aria-hidden": "true" },
    h("div", { class: "sk-hero" }), h("div", { class: "sk-line wide" }), h("div", { class: "sk-line" }), h("div", { class: "sk-line short" }));
  return [
    h("div", { class: "head" }, h("div", null, h("h1", null, "Лоты"), h("p", { class: "sk-text", "aria-label": "Загрузка" }, "Загружаю предложения"))),
    h("div", { class: "toolbar", "aria-hidden": "true" }, h("div", { class: "sk-seg" })),
    h("div", { class: "offers", role: "status", "aria-label": "Загрузка" }, card(), card(), card(), card()),
  ];
}

// Pictures that have already loaded once are shown at once on re-render, without the fade-in.
const loadedImages = new Set();

/** The gift picture (it already contains backdrop, pattern and gift); until it loads, or when it cannot be
 *  loaded (unknown slug, CDN down), the backdrop colours and a gift outline stand in for it. */
function offerArt(o, extraClass) {
  const banner = h("div", { class: "banner" + (extraClass ? " " + extraClass : "") });
  const center = safeColor(o.backdrop_center);
  const edge = safeColor(o.backdrop_edge);
  if (center && edge) banner.style.background = `radial-gradient(120% 120% at 50% 35%, ${center}, ${edge})`;
  if (center && edge && hexLuma(center) * 0.6 + hexLuma(edge) * 0.4 > 150) banner.classList.add("on-light");
  banner.append(h("span", { class: "placeholder", "aria-hidden": "true" }, icon("offers")));
  const imageUrl = safeImageUrl(o.image_url);
  if (imageUrl) {
    const img = h("img", { class: "art", src: imageUrl, alt: "", loading: "lazy", decoding: "async" });
    if (loadedImages.has(imageUrl)) banner.classList.add("has-image");
    else {
      banner.classList.add("loading-image");
      img.addEventListener("load", () => { loadedImages.add(imageUrl); banner.classList.remove("loading-image"); banner.classList.add("has-image"); });
      img.addEventListener("error", () => { img.remove(); banner.classList.remove("loading-image"); });
    }
    banner.append(img);
  }
  banner.append(h("span", { class: "hero-chips" },
    isNew(o) ? h("span", { class: "badge-new" }, "Новый") : null,
    h("span", { class: "chip-market" }, o.market_title)));
  return banner;
}

function tierChip(rarity) {
  const tier = rarityTier(rarity);
  return tier ? h("span", { class: "tier tier-" + tier, title: TIER_TITLES[tier] || null }, pct(rarity)) : null;
}

/** Compact tile for the list: picture, name, model and price. Everything else is in the card it opens. */
function offerCard(o, index) {
  const art = offerArt(o);
  const rarest = rarestOf(o);
  const tier = rarityTier(rarest === Infinity ? null : rarest);
  if (tier && tier !== "common") { // the rarest attribute, only when it is actually rare (10% or less)
    const chip = tierChip(rarest);
    chip.classList.add("on-art");
    art.append(chip);
  }
  const card = h("button", { type: "button", class: "offer", "aria-haspopup": "dialog", onclick: () => { haptic(); openDetail(o); } },
    art,
    h("span", { class: "body" },
      h("span", { class: "title" }, o.collection),
      h("span", { class: "sub" }, ["№ " + o.number, o.model].filter(Boolean).join(" · ")),
      h("span", { class: "buy" }, priceNode(o))));
  card.style.setProperty("--i", String(Math.min(index || 0, 8)));
  return card;
}

function traitsList(o) {
  const center = safeColor(o.backdrop_center);
  const edge = safeColor(o.backdrop_edge);
  const traits = h("dl", { class: "traits" });
  for (const [label, key] of TRAITS) {
    if (!o[key]) continue;
    const name = h("dd", null, o[key]);
    if (key === "backdrop" && center && edge) {
      const dot = h("i", { class: "swatch", "aria-hidden": "true" });
      dot.style.background = `radial-gradient(circle at 35% 30%, ${center}, ${edge})`;
      name.prepend(dot);
    }
    traits.append(h("dt", null, label), name, h("dd", { class: "rar" }, tierChip(o[key + "_rarity"])));
  }
  return traits;
}

function ownerLine(o) {
  const ownerUrl = safeTgUrl(o.owner_url);
  return h("div", { class: "owner" }, "Владелец: ",
    ownerUrl ? h("a", { href: ownerUrl, onclick: (e) => { e.preventDefault(); openLink(ownerUrl); } }, "@" + o.owner_username)
      : o.owner_name ? `${o.owner_name} (профиль скрыт)` : "скрыт");
}

/** Full card of one offer: big picture, all attributes, where and when, and what can be done with it. */
function detailView() {
  const o = state.detail;
  const art = offerArt(o, "detail-art");
  art.append(h("button", { type: "button", class: "icon-btn on-art", "aria-label": "Закрыть", onclick: closeDetail }, icon("close")));

  const marketUrl = safeTgUrl(o.market_url);
  const giftUrl = safeTgUrl(o.url);
  const seen = o.seen_at ? "замечен " + timeAgo(o.seen_at) : null;
  const actions = h("div", { class: "actions" },
    marketUrl ? h("button", { type: "button", class: "btn wide", onclick: () => { haptic(); openLink(marketUrl); } }, icon("cart"), `Купить на ${o.market_title}`) : null,
    giftUrl ? h("button", { type: "button", class: marketUrl ? "btn wide quiet" : "btn wide", onclick: () => { haptic(); openLink(giftUrl); } },
      icon("link"), marketUrl ? "Страница подарка" : "Открыть подарок в Telegram") : null,
    h("button", { type: "button", class: "btn wide quiet", onclick: () => followSimilar(o) }, icon("bell"), "Следить за такими"));

  return h("div", { class: "scrim", onclick: (e) => { if (e.target === e.currentTarget) closeDetail(); } },
    h("div", { class: "sheet detail", role: "dialog", "aria-modal": "true", "aria-label": `${o.collection} № ${o.number}` },
      h("div", { class: "grabber", "aria-hidden": "true" }),
      art,
      h("div", { class: "detail-info" },
        h("div", { class: "detail-head" },
          h("h2", null, o.collection),
          h("p", { class: "sub" }, ["№ " + o.number, seen].filter(Boolean).join(" · "))),
        h("div", { class: "detail-price" }, priceNode(o), unpriced(o) ? null : h("span", { class: "where" }, "на " + o.market_title)),
        traitsList(o),
        ownerLine(o),
        actions,
        h("p", { class: "fine" }, "Бот сделок не проводит. Цену и наличие проверьте на странице подарка."))));
}

function statusText() {
  const shown = state.offers && state.offers.has_filters
    ? (state.view === "all" ? state.offers.total : state.offers.offers.filter(isFresh).length) : 0;
  const found = state.offers && state.offers.has_filters ? `${state.view === "all" ? "Найдено" : "Новых"} ${shown}.` : "";
  return `${found} Обновлено ${agoMs(state.updatedAt)}.`.trim();
}
function updateStatus() {
  const el = document.getElementById("status");
  if (el) el.textContent = statusText();
}

const countBadge = (n) => (n == null ? null : [" ", h("span", { class: "count" }, n)]);

function segmented(label, options, current, onPick, extraClass) {
  return h("div", { class: "seg" + (extraClass ? " " + extraClass : ""), role: "group", "aria-label": label },
    options.map(([value, text, count]) => h("button", { type: "button", class: "seg-btn", "aria-pressed": String(current === value),
      onclick: () => { if (current !== value) { haptic("select"); onPick(value); } } }, text, countBadge(count))));
}

function searchBox() {
  return h("div", { class: "search" }, icon("search"),
    h("input", { id: "q", type: "search", value: state.query, placeholder: "Коллекция, модель, фон или №", "aria-label": "Поиск по лотам",
      autocomplete: "off", spellcheck: "false", enterkeyhint: "search",
      oninput: (e) => { state.query = e.target.value; renderOfferList(); },
      onkeydown: (e) => { if (e.key === "Enter") e.target.blur(); } })); // hides the phone keyboard
}

function offerListContent() {
  const o = state.offers;
  const list = visibleOffers();
  if (list.length) return [h("div", { class: "offers" }, list.map(offerCard))];
  if (state.query.trim()) {
    return [emptyState("search", "Ничего не нашлось", `По запросу «${state.query.trim()}» лотов нет. Проверьте написание или сбросьте поиск.`,
      "Сбросить поиск", () => { state.query = ""; render(); })];
  }
  if (state.view === "new" && !o.offers.some(isFresh)) {
    return [emptyState("radar", "Новых лотов пока нет",
      "Здесь только подарки, выставленные уже после начала слежения (первый обход только запоминает то, что уже продаётся). Как только выставят новый, он появится здесь сам.",
      "Показать все", () => setView("all"))];
  }
  return [emptyState("offers", "На этой площадке пока пусто", "Выберите «Все площадки» или другую площадку.")];
}

// Typing in the search box re-renders only the list, so the input keeps focus and the keyboard stays open.
function renderOfferList() {
  const host = document.getElementById("offer-list");
  if (host) host.replaceChildren(...offerListContent());
}

function refreshNotice() {
  if (!state.refreshError) return null;
  return h("div", { class: "notice", role: "alert" }, icon("alert"),
    h("span", { class: "notice-text" }, h("b", null, "Не удалось обновить. "), `Показаны данные, обновлённые ${agoMs(state.updatedAt)}.`),
    h("button", { type: "button", class: "notice-btn", disabled: state.refreshing, onclick: () => { haptic(); refreshOffers(false); } }, "Повторить"));
}

function offersView() {
  const o = state.offers;
  const head = h("div", { class: "head" },
    h("div", null, h("h1", null, "Лоты"),
      h("p", { class: "status" }, h("i", { class: "live-dot" + (state.refreshing ? " busy" : state.refreshError ? " stale" : ""), "aria-hidden": "true" }),
        h("span", { id: "status" }, statusText()))),
    h("button", { class: "icon-btn" + (state.refreshing ? " spinning" : ""), "aria-label": "Обновить", disabled: state.refreshing,
      onclick: () => { haptic(); refreshOffers(false); } }, icon("refresh")));

  if (!o.has_filters) {
    if (state.filters && state.filters.length) {
      return [head, refreshNotice(), demoNote(), emptyState("filters", "Все фильтры выключены", "Включите хотя бы один фильтр, и здесь появятся подходящие предложения.",
        "К фильтрам", () => { state.tab = "filters"; render(); })];
    }
    return [head, refreshNotice(), demoNote(), onboarding()];
  }
  if (!o.offers.length) {
    return [head, refreshNotice(), demoNote(), emptyState("radar", "Ничего не найдено", `Я показываю предложения, замеченные за последние ${state.me.fresh_minutes} мин. Новые подходящие лоты придут уведомлением в чат с ботом, если оно включено.`)];
  }

  const markets = [...new Set(o.offers.map((x) => x.market))];
  const inView = (x) => state.view === "all" || isFresh(x);
  const count = (m) => o.offers.filter((x) => (m === "all" || x.market === m) && inView(x)).length;
  const titles = Object.fromEntries(o.offers.map((x) => [x.market, x.market_title]));
  const toolbar = h("div", { class: "toolbar" },
    segmented("Что показывать", [["new", "Новые", o.offers.filter(isFresh).length], ["all", "Все", o.offers.length]], state.view, setView, "seg-view"),
    o.offers.length >= SEARCH_FROM || state.query ? searchBox() : null,
    h("div", { class: "toolbar-row" },
      segmented("Сортировка", Object.entries(SORTS).map(([k, v]) => [k, v.label]), state.sort, (v) => { state.sort = v; render(); }, "seg-sort"),
      markets.length > 1
        ? h("div", { class: "chips", role: "group", "aria-label": "Площадка" },
          [["all", "Все площадки"], ...markets.map((m) => [m, titles[m]])].map(([m, text]) =>
            h("button", { type: "button", class: "chip", "aria-pressed": String(state.market === m),
              onclick: () => { haptic("select"); state.market = m; render(); } }, text, countBadge(count(m)))))
        : null));

  return [head, refreshNotice(), demoNote(), toolbar, h("div", { id: "offer-list" }, offerListContent())];
}

function filtersView() {
  const head = h("div", { class: "head" },
    h("div", null, h("h1", null, "Фильтры"), h("p", null, "Лот подходит, если выполнены все условия одного включённого фильтра.")),
    h("button", { class: "btn with-icon", onclick: () => openSheet(null) }, icon("plus"), "Новый"));

  const body = state.filters.length
    ? h("div", { class: "list" }, state.filters.map((f) => filterRow(f)))
    : onboarding();
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
  const head = h("div", { class: "head" }, h("div", null, h("h1", null, "Профиль")));

  const card = h("section", { class: "profile-card" },
    h("div", { class: "avatar", "aria-hidden": "true" }, (name.trim()[0] || "?").toUpperCase()),
    h("div", { class: "who" }, h("div", { class: "hello" }, "Привет, " + name + "!"),
      h("div", { class: "sub" }, m.user.username ? "@" + m.user.username : "Слежу за рынком подарков для вас")));

  const tile = (label, value, sub) => h("div", { class: "tile" },
    h("div", { class: "tile-label" }, label), h("div", { class: "tile-value" }, fmt(value)), h("div", { class: "tile-sub" }, sub));
  const tiles = h("div", { class: "tiles" },
    tile("Фильтры", s.filters_total, `включено ${s.filters_active}`),
    tile("Уведомления", s.notifications_received, "получено от бота"));

  const notify = h("div", { class: "panel" }, h("div", { class: "setting" },
    h("span", { class: "setting-icon" }, icon("bell")),
    h("div", { class: "main" }, h("div", { class: "name" }, "Уведомления в чат"),
      h("div", { class: "sub" }, "Присылать новые подходящие лоты в чат с ботом.")),
    h("label", { class: "switch" },
      h("input", { type: "checkbox", role: "switch", checked: m.notifications_enabled, "aria-label": "Уведомления в чат",
        onchange: (e) => toggleNotifications(e.target.checked, e.target) }), h("i"))));

  const facts = (title, rows) => h("section", { class: "panel" }, h("h2", { class: "panel-title" }, title),
    h("dl", { class: "facts" }, rows.flatMap(([k, v]) => [h("dt", null, k), h("dd", null, String(v))])));
  return [head, demoNote(), h("div", { class: "profile" }, card, tiles, notify,
    facts("Рынок", [["Последний опрос", timeAgo(s.last_poll_at)], ["Известных предложений", fmt(s.listings_known)], ["Пользователей бота", fmt(s.users)]]),
    h("p", { class: "fine" }, "Бот проверяет рынок по расписанию, поэтому уведомления приходят с задержкой. Сделки проходят в Telegram по ссылке на подарок, бот их не проводит."))];
}

function sheetView() {
  const { filter: f, draft } = state.sheet;
  const src = f || draft || {};
  const val = (key) => (src[key] != null ? String(src[key]) : "");
  const input = (id, label, value, attrs) => h("div", { class: "field" }, h("label", { for: id }, label),
    h("input", { id, name: id, value, autocomplete: "off", enterkeyhint: id === "max_rarity" ? "done" : "next", ...attrs }));

  // Enter moves to the next field (on a phone it would otherwise save a half-filled filter); the last one saves
  const nextField = (e) => {
    if (e.key !== "Enter" || e.target.tagName !== "INPUT") return;
    const fields = [...e.currentTarget.querySelectorAll("input")];
    const next = fields[fields.indexOf(e.target) + 1];
    if (next) { e.preventDefault(); next.focus(); }
  };
  const form = h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); saveFilter(e.target); }, onkeydown: nextField },
    draft ? h("p", { class: "hint" }, "Условия подставлены. Поправьте их, если нужно, и сохраните.") : null,
    input("name", "Название", val("name"), { maxlength: "64", placeholder: "Например, Дешёвый Pepe" }),
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
    input("max_rarity", "Редкость не больше, %", src.max_rarity != null ? String(src.max_rarity / 10) : "",
      { inputmode: "decimal", placeholder: "Любая, например 2" }),
    f ? h("button", { class: "btn danger wide", type: "button", onclick: () => removeFilter(f) }, "Удалить фильтр") : null,
    // stays pinned to the bottom of the sheet, so "Save" is reachable with the phone keyboard open
    h("div", { class: "actions" }, h("button", { class: "btn wide", type: "submit" }, "Сохранить")));

  return h("div", { class: "scrim", onclick: (e) => { if (e.target === e.currentTarget) closeSheet(); } },
    h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": f ? "Редактирование фильтра" : "Новый фильтр" },
      h("div", { class: "grabber", "aria-hidden": "true" }),
      h("div", { class: "top" }, h("h2", null, f ? "Фильтр" : "Новый фильтр"),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Закрыть", onclick: closeSheet }, icon("close"))),
      form));
}

function tabbar() {
  const offers = state.offers && state.offers.has_filters ? state.offers.offers : [];
  const total = state.view === "all" ? offers.length : offers.filter(isFresh).length;
  const tab = (id, label) => h("button", { class: "tab", role: "tab", "aria-selected": String(state.tab === id),
    onclick: () => { if (state.tab !== id) { haptic("select"); state.tab = id; render(); window.scrollTo(0, 0); } } },
    h("span", { class: "tab-icon" }, icon(id), id === "offers" && total ? h("b", { class: "tab-badge" }, total > 99 ? "99+" : total) : null), label);
  return h("nav", { class: "tabbar", role: "tablist" }, h("div", { class: "inner" },
    tab("offers", "Лоты"), tab("filters", "Фильтры"), tab("profile", "Профиль")));
}

function render() {
  const root = document.getElementById("app");
  const page = h("main", { class: "page" });
  // a background refresh must not steal the focus (and the phone keyboard) from the search box
  const active = document.activeElement;
  const focusId = active && active.id && !active.closest("#overlay") ? active.id : null;
  const caret = focusId && typeof active.selectionStart === "number" ? active.selectionStart : null;

  if (!tg || !tg.initData) {
    page.append(emptyState("offers", "Откройте через Telegram", "Это мини-приложение работает внутри Telegram: запустите его из чата с ботом."));
  } else if (state.error) {
    page.append(emptyState("alert", "Не удалось загрузить данные", state.error, "Повторить", loadAll));
  } else if (!state.me) {
    page.append(...skeletonView());
  } else {
    const views = { offers: offersView, filters: filtersView, profile: profileView };
    page.append(...views[state.tab]().filter(Boolean));
  }

  root.replaceChildren(h("div", { class: "shell" }, page, state.me ? tabbar() : null, h("div", { id: "overlay" })));
  renderOverlay();
  const again = focusId && document.getElementById(focusId);
  if (again) {
    again.focus({ preventScroll: true });
    if (caret != null) try { again.setSelectionRange(caret, caret); } catch (_) { /* not a text input */ }
  }
}

/** Sheets live in their own layer: opening or closing one does not rebuild (and re-fade) the list under it. */
function renderOverlay() {
  const host = document.getElementById("overlay");
  if (!host) return;
  host.replaceChildren(...[state.sheet ? sheetView() : state.detail ? detailView() : null].filter(Boolean));
  const open = Boolean(state.sheet || state.detail);
  document.documentElement.classList.toggle("locked", open); // the page behind a sheet does not scroll
  try { // Telegram's own back button (and Android's back gesture) closes the sheet instead of the whole app
    if (open) tg.BackButton.show();
    else tg.BackButton.hide();
  } catch (_) { /* outside Telegram or an old client */ }
}

/* ── actions ── */

// The sheet is updated in place (not re-rendered) so a failed save never wipes what was typed.
function sheetBusy(busy) {
  for (const button of document.querySelectorAll(".sheet .form button")) button.disabled = busy;
}
function sheetError(message) {
  const form = document.querySelector(".sheet .form");
  if (!form) return;
  const old = form.querySelector(".err");
  if (old) old.remove();
  const error = h("p", { class: "err", role: "alert" }, message);
  form.insertBefore(error, form.querySelector(".actions"));
  error.scrollIntoView({ block: "nearest" });
}

function openSheet(filter, draft) {
  state.detail = null;
  state.sheet = { filter, draft: filter ? null : draft || null };
  renderOverlay();
  // on phones focusing a field pops the keyboard over half the form, so only do it on wide screens
  if (window.matchMedia("(min-width: 700px)").matches) {
    const first = document.getElementById(filter ? "collection" : "name");
    if (first) first.focus();
  }
}
function closeSheet() {
  state.sheet = null;
  renderOverlay();
}
function openDetail(offer) {
  state.detail = offer;
  renderOverlay();
}
function closeDetail() {
  state.detail = null;
  renderOverlay();
}
function closeTopLayer() {
  if (state.sheet) closeSheet();
  else if (state.detail) closeDetail();
}

/** "Следить за такими": a new filter pre-filled with this gift's collection and model. */
function followSimilar(o) {
  haptic();
  const name = [o.collection, o.model].filter(Boolean).join(" · ").slice(0, 64);
  state.tab = "filters";
  state.detail = null;
  render();
  window.scrollTo(0, 0);
  openSheet(null, { name, collection: o.collection, model: o.model });
}

function readForm(form) {
  const text = (id) => form.elements[id].value.trim() || null;
  const rarity = form.elements.max_rarity.value.trim().replace(",", ".").replace(/%$/, "");
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

document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeTopLayer(); });

// Keep the list fresh while the app is open: refresh once a minute, and keep "updated N min ago" honest.
const due = () => Date.now() - (state.updatedAt || 0) >= REFRESH_MS;
setInterval(() => {
  if (document.visibilityState !== "visible" || !state.me || state.sheet || state.detail) return;
  if (due()) refreshOffers(true);
  else if (state.tab === "offers") updateStatus();
}, 15_000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.me && due()) refreshOffers(true);
});
window.addEventListener("online", () => { // back from a tunnel hiccup or a lost connection
  if (state.error) loadAll();
  else if (state.me && (state.refreshError || due())) refreshOffers(true);
});

if (tg) {
  tg.ready();
  tg.expand();
  applyTheme();
  tg.onEvent("themeChanged", applyTheme);
  try { tg.BackButton.onClick(closeTopLayer); } catch (_) { /* old client without BackButton */ }
}
if (tg && tg.initData) loadAll();
else render();
