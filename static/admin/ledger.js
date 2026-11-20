/* FestPOS – Verwaltung › Begriffe des Kassenbuchs (Zahlart, Storno-Grund, MWST) – für Bestellungen, Abschluss, Kasse, Produkte. */

export const PAY_OPTS = [["cash", "ledger.pay.cash"], ["card", "ledger.pay.card"], ["twint", "ledger.pay.twint"], ["other", "ledger.pay.other"],
  ["payrexx", "ledger.pay.payrexx"]];
export const payName = (v) => t(PAY_OPTS.find(([x]) => x === v)?.[1] || "ledger.pay.none");
export const REASON_OPTS = [["wrong", "ledger.reason.wrong"], ["unpaid", "ledger.reason.unpaid"],
  ["complaint", "ledger.reason.complaint"], ["other", "ledger.reason.other"]];
export const VAT_OPTS = [["normal", "ledger.vat.normal"], ["reduced", "ledger.vat.reduced"], ["lodging", "ledger.vat.lodging"]];
export const vatName = (v) => t(VAT_OPTS.find(([x]) => x === v)?.[1] || "ledger.vat.normal");
export const whoName = (v) => (v && v.startsWith("device:") ? t("ledger.who.device", { name: v.slice(7) }) : v || "–");
export const orderNo = (id) => `#${String(id).padStart(4, "0")}`;
export const pct = (bp) => `${(bp / 100).toLocaleString(I18n.locale, { minimumFractionDigits: 1, maximumFractionDigits: 2 })} %`;
export const newId = () => (crypto.randomUUID ? crypto.randomUUID()
  : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => ((Math.random() * 16) | (c === "x" ? 0 : 8)).toString(16)));
