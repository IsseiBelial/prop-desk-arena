// Character sprites: small front-facing figures drawn in SVG, origin at the feet.

const SKIN = {
  scout: "#c68e63", newshound: "#f1c9a5", bear: "#a86b45", oracle: "#e8b98f", fundamental: "#7a4b2e",
  momentum: "#f3cfae", cloud: "#eac096", flow: "#8d5a3b", news: "#f6d5bb", risk: "#d9a37c", chair: "#b47a52",
};

function shade(hex, amt) {
  const n = parseInt(hex.slice(1), 16);
  const c = [n >> 16, (n >> 8) & 255, n & 255].map((v) => Math.max(0, Math.min(255, Math.round(v + amt * 255))));
  return "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("");
}

const ACCESSORY = {
  goggles: (c) => `<rect x="-12" y="-47" width="24" height="4" rx="2" fill="#334155"/>
    <circle cx="-5" cy="-45" r="4" fill="#38bdf8" stroke="#0f172a" stroke-width="1.5"/>
    <circle cx="5" cy="-45" r="4" fill="#38bdf8" stroke="#0f172a" stroke-width="1.5"/>`,
  newspaper: () => `<g transform="translate(10,-24) rotate(12)"><rect width="13" height="16" fill="#f8fafc" stroke="#94a3b8"/>
    <rect x="2" y="2" width="9" height="3" fill="#475569"/><rect x="2" y="7" width="9" height="1.5" fill="#94a3b8"/>
    <rect x="2" y="10" width="7" height="1.5" fill="#94a3b8"/></g>`,
  bear: () => `<circle cx="-10" cy="-50" r="5" fill="#7c4a2d"/><circle cx="10" cy="-50" r="5" fill="#7c4a2d"/>
    <circle cx="-10" cy="-50" r="2.4" fill="#d4a373"/><circle cx="10" cy="-50" r="2.4" fill="#d4a373"/>
    <ellipse cx="0" cy="-36" rx="5" ry="3.4" fill="#e9c9a3"/><circle cx="0" cy="-37" r="1.6" fill="#1f2937"/>`,
  visor: () => `<rect x="-12" y="-44" width="24" height="5" rx="2.5" fill="#2dd4bf" opacity=".9"/>
    <rect x="-12" y="-44" width="24" height="2" rx="1" fill="#ccfbf1" opacity=".7"/>`,
  glasses: () => `<circle cx="-5" cy="-41" r="3.6" fill="none" stroke="#0f172a" stroke-width="1.4"/>
    <circle cx="5" cy="-41" r="3.6" fill="none" stroke="#0f172a" stroke-width="1.4"/>
    <line x1="-1.4" y1="-41" x2="1.4" y2="-41" stroke="#0f172a" stroke-width="1.4"/>`,
  headband: () => `<rect x="-12" y="-49" width="24" height="4" fill="#ef4444"/>
    <path d="M11 -48 l7 -3 l-2 5 z" fill="#ef4444"/>`,
  cloudhat: () => `<g fill="#f8fafc" stroke="#cbd5e1" stroke-width=".8"><circle cx="-6" cy="-53" r="5"/>
    <circle cx="1" cy="-56" r="6"/><circle cx="8" cy="-52" r="4.5"/></g>`,
  badge: () => `<circle cx="-4" cy="-21" r="4.2" fill="#facc15" stroke="#a16207"/><text x="-4" y="-18.6" font-size="6"
    text-anchor="middle" font-weight="900" fill="#713f12">$</text>`,
  headset: () => `<path d="M-12 -40 a12 12 0 0 1 24 0" fill="none" stroke="#111827" stroke-width="2.4"/>
    <rect x="-14" y="-43" width="4" height="7" rx="2" fill="#111827"/><rect x="10" y="-43" width="4" height="7" rx="2" fill="#111827"/>
    <path d="M12 -37 q0 6 -7 6" fill="none" stroke="#111827" stroke-width="1.4"/>`,
  helmet: () => `<path d="M-13 -44 a13 12 0 0 1 26 0 z" fill="#facc15" stroke="#a16207"/>
    <rect x="-15" y="-45" width="30" height="3" rx="1.5" fill="#eab308"/>`,
  crown: () => `<path d="M-10 -50 l3 -9 l4 6 l3 -8 l3 8 l4 -6 l3 9 z" fill="#fbbf24" stroke="#b45309" stroke-width="1"/>
    <circle cx="0" cy="-55" r="1.6" fill="#ef4444"/>`,
};

// Returns SVG markup for one character. `scale` sizes it; legs animate via the .walking class.
export function sprite(agent, scale = 1) {
  const c = agent.color, skin = SKIN[agent.id] || "#e5c29f";
  const acc = (ACCESSORY[agent.look] || (() => ""))(c);
  return `<g class="sprite" transform="scale(${scale})">
    <ellipse class="shadow" cx="0" cy="0" rx="13" ry="4.5" fill="#000" opacity=".35"/>
    <rect class="leg legL" x="-7" y="-11" width="5.5" height="11" rx="2.5" fill="${shade(c, -0.35)}"/>
    <rect class="leg legR" x="1.5" y="-11" width="5.5" height="11" rx="2.5" fill="${shade(c, -0.35)}"/>
    <rect x="-11" y="-31" width="22" height="22" rx="8" fill="${c}"/>
    <rect x="-7" y="-27" width="5" height="12" rx="2.5" fill="#fff" opacity=".18"/>
    <circle cx="0" cy="-41" r="12" fill="${skin}"/>
    <circle cx="-4.2" cy="-41" r="1.7" fill="#0f172a"/><circle cx="4.2" cy="-41" r="1.7" fill="#0f172a"/>
    <path d="M-3.5 -35.5 q3.5 2.6 7 0" fill="none" stroke="#0f172a" stroke-width="1.3" stroke-linecap="round"/>
    ${acc}
    <g class="think"><rect x="-12" y="-74" width="24" height="12" rx="6" fill="#0b1220" stroke="${c}"/>
      <circle cx="-5" cy="-68" r="1.8" fill="${c}"/><circle cx="0" cy="-68" r="1.8" fill="${c}"/><circle cx="5" cy="-68" r="1.8" fill="${c}"/></g>
  </g>`;
}

export { shade };
