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

const thinkDots = (c, y) => `<g class="think"><rect x="-12" y="${y}" width="24" height="12" rx="6" fill="#0b1220" stroke="${c}"/>
  <circle cx="-5" cy="${y + 6}" r="1.8" fill="${c}"/><circle cx="0" cy="${y + 6}" r="1.8" fill="${c}"/><circle cx="5" cy="${y + 6}" r="1.8" fill="${c}"/></g>`;

// The Ascendant: floating robed seers with a glowing head and a halo.
export function seer(agent, scale = 1) {
  const c = agent.color, id = "sg" + agent.id;
  const crown = agent.look === "crown"
    ? `<path d="M-9 -60 l3 -9 l3 6 l3 -9 l3 9 l3 -6 l3 9 z" fill="#fbbf24" stroke="#92400e" stroke-width=".8"/>` : "";
  return `<g class="sprite" transform="scale(${scale})"><defs>
      <linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${c}"/><stop offset="1" stop-color="${shade(c, -0.45)}"/></linearGradient>
      <radialGradient id="${id}o"><stop offset="0" stop-color="#fff"/><stop offset=".5" stop-color="${c}"/><stop offset="1" stop-color="${c}" stop-opacity="0"/></radialGradient></defs>
    <ellipse class="shadow" cx="0" cy="0" rx="11" ry="3.5" fill="#000" opacity=".25"/>
    <g class="hover">
      <path d="M0 -40 C 9 -36 12 -20 15 -6 L 0 -10 L -15 -6 C -12 -20 -9 -36 0 -40 Z" fill="url(#${id})" stroke="${shade(c, 0.3)}" stroke-width=".8"/>
      <path d="M-11 -32 q11 -6 22 0 l-3 4 q-8 -4 -16 0 z" fill="${shade(c, 0.25)}"/>
      <circle cx="0" cy="-45" r="13" fill="url(#${id}o)" opacity=".7"/>
      <circle cx="0" cy="-45" r="7.5" fill="#0b0820" stroke="${c}" stroke-width="1.5"/>
      <rect x="-4.5" y="-46.5" width="3" height="1.6" rx=".8" fill="#67e8f9"/><rect x="1.5" y="-46.5" width="3" height="1.6" rx=".8" fill="#67e8f9"/>
      <ellipse cx="0" cy="-57" rx="9" ry="2.6" fill="none" stroke="${c}" stroke-width="1.6" opacity=".9"/>
      ${crown}
      <path d="M-17 -18 l-3 -5 l3 -5 l3 5 z" fill="#a5f3fc" opacity=".85"/><path d="M17 -18 l-3 -5 l3 -5 l3 5 z" fill="#a5f3fc" opacity=".85"/>
    </g>
    ${thinkDots(c, -82)}
  </g>`;
}

// The Brood: small skittering drones.
export function drone(agent, scale = 1) {
  const c = agent.color;
  const legs = [-6, 0, 6].map((x) => `<line x1="${x}" y1="-6" x2="${x * 1.6 - 5}" y2="0" /><line x1="${x}" y1="-6" x2="${x * 1.6 + 5}" y2="0" />`).join("");
  return `<g class="sprite" transform="scale(${scale})">
    <ellipse class="shadow" cx="0" cy="0" rx="12" ry="3.5" fill="#000" opacity=".3"/>
    <g class="legs" stroke="${shade(c, -0.4)}" stroke-width="1.6" stroke-linecap="round">${legs}</g>
    <ellipse cx="0" cy="-9" rx="11" ry="7" fill="${shade(c, -0.25)}"/>
    <ellipse cx="0" cy="-10" rx="9" ry="5.5" fill="${c}"/>
    <path d="M-6 -13 q6 -3 12 0" fill="none" stroke="${shade(c, 0.35)}" stroke-width="1.2"/>
    <ellipse cx="8" cy="-12" rx="5" ry="4.2" fill="${shade(c, -0.15)}"/>
    <circle cx="9.5" cy="-13" r="1.4" fill="#bef264"/><circle cx="6.5" cy="-13.4" r="1.2" fill="#bef264"/>
    <path d="M12 -10 l4 1 M12 -12 l4 -1" stroke="${shade(c, -0.4)}" stroke-width="1.2"/>
    <circle class="vote-glow" cx="0" cy="-10" r="12" fill="none" stroke-width="2.5" opacity="0"/>
    ${thinkDots(c, -36)}
  </g>`;
}

// The Hive Queen.
export function queen(agent, scale = 1) {
  const c = agent.color;
  const legs = [-10, -2, 6].map((x) => `<path d="M${x} -14 q-6 6 -12 14" /><path d="M${x} -14 q8 6 14 14" />`).join("");
  return `<g class="sprite" transform="scale(${scale})">
    <ellipse class="shadow" cx="0" cy="0" rx="26" ry="6" fill="#000" opacity=".35"/>
    <g fill="none" stroke="${shade(c, -0.45)}" stroke-width="2.4" stroke-linecap="round" class="legs">${legs}</g>
    <ellipse cx="-12" cy="-18" rx="17" ry="12" fill="${shade(c, -0.3)}"/>
    <ellipse cx="-12" cy="-19" rx="11" ry="7" fill="#f9a8d4" opacity=".55" class="belly"/>
    <ellipse cx="6" cy="-26" rx="10" ry="12" fill="${c}"/>
    <path d="M0 -36 l-4 -14 l7 9 l2 -16 l4 15 l6 -11 l-2 15 z" fill="${shade(c, -0.2)}"/>
    <ellipse cx="14" cy="-34" rx="7" ry="6" fill="${shade(c, -0.1)}"/>
    <circle cx="16" cy="-35" r="1.8" fill="#bef264"/><circle cx="12" cy="-36" r="1.6" fill="#bef264"/>
    <path d="M19 -31 l6 2 M19 -33 l6 -2" stroke="${shade(c, -0.4)}" stroke-width="1.6"/>
    ${thinkDots(c, -70)}
  </g>`;
}

// Picks the right body for any unit.
export function unitSprite(agent, scale = 1) {
  if (agent.style === "seer") return seer(agent, scale);
  if (agent.style === "drone") return drone(agent, scale);
  if (agent.style === "queen") return queen(agent, scale);
  return sprite(agent, scale);
}
