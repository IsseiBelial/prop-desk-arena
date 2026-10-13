// Unit callouts. All lines are original to Prop Desk. Speech uses the browser's built-in
// speech synthesis (no audio files); blips are synthesized with WebAudio.

const FACTION_LINES = {
  legion: {
    turn: ["Iron Legion, reporting for duty.", "Boots on the ground. Let's make some money.", "Legion online. Awaiting orders."],
    research: ["Scouting the field.", "Running the numbers, sir.", "Eyes on target."],
    yes: ["Locked and loaded.", "Green light from me.", "I'm in. Let's roll."],
    no: ["Negative. Not my kind of trade.", "I'm out on this one.", "Hold your fire."],
    pass: ["No edge, no play.", "Sitting this one out."],
    approve: ["Council approves. Move out!", "It's unanimous enough. Go, go, go."],
    reject: ["Stand down.", "Request denied. We wait."],
    trade: ["Order's in. They'll never see it coming.", "Position secured, sir.", "Target acquired."],
    retreat: ["Pulling back!", "Fall back, fall back."],
    win: ["Legion takes the round!", "Discipline wins again."],
  },
  ascendant: {
    turn: ["The Ascendant awakens.", "We see what was, and what will be.", "The patterns call."],
    research: ["Reading the patterns.", "The data sings to us.", "Every field, at once."],
    yes: ["Probability favors us.", "The path is clear."],
    no: ["The odds are against it.", "This path ends poorly."],
    pass: ["The pattern is unclear.", "Not yet."],
    approve: ["Convergence achieved.", "It is foreseen."],
    reject: ["The Convergence is incomplete.", "Patience. The pattern is not ready."],
    trade: ["It is done.", "As the models foretold."],
    retreat: ["The tide has turned. We withdraw.", "Recalculating."],
    win: ["As it was always going to be.", "Probability is destiny."],
  },
  brood: {
    turn: ["The swarm stirs.", "We hunger.", "Many. Hungry. Awake."],
    research: ["Spreading across the fields.", "Tasting the signals.", "Skitter, skitter."],
    yes: ["Feed!", "The swarm agrees."],
    no: ["Rotten. Leave it.", "The swarm recoils."],
    pass: ["Not ripe.", "Wait. Watch."],
    approve: ["Consume!", "The swarm has spoken."],
    reject: ["Scatter.", "The hive is not hungry."],
    trade: ["The hive grows.", "Devour it."],
    retreat: ["Abandon the field!", "Retreat to the hive."],
    win: ["The swarm is victorious.", "We are many. We are right."],
  },
  neutral: {
    approve: ["Risk approved. Trade safe.", "Within limits. Cleared."],
    veto: ["Veto. Too hot.", "Not on my watch.", "Limits say no."],
    contest: ["Contested field!", "Factions clash on this one."],
  },
};

const UNIT_LINES = {
  scout: { research: ["Scout online.", "Sweeping the watchlist."] },
  newshound: { research: ["Hot off the wire.", "Checking the headlines."] },
  bear: { any: ["I'll tell you why this fails.", "Somebody has to say it.", "Not so fast."] },
  oracle: { research: ["Scanning the prediction markets.", "Odds are my business."] },
  cloud: { pass: ["No cloud, no trade."], yes: ["Above the cloud. I'm in."] },
  momentum: { yes: ["Momentum's on our side!"], no: ["Trend's broken."] },
  news: { yes: ["The news is good. Go!"], no: ["Bad headlines. Hard pass."] },
  fundamental: { yes: ["The numbers check out."], no: ["Too expensive for me."] },
  flow: { yes: ["Follow the money. It's coming in."], no: ["Money's leaving the building."] },
  chair: { approve: ["Council approves. Execute."], reject: ["Motion denied."] },
  vexa: { any: ["Many signals. One will."] },
  thalor: { any: ["The Convergence speaks."] },
  risk: {},
};

const VOICE = {
  legion: { pitch: 0.85, rate: 1.08 },
  ascendant: { pitch: 0.45, rate: 0.82 },
  brood: { pitch: 1.75, rate: 1.22 },
  neutral: { pitch: 1.1, rate: 1.0 },
};

const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

export class Voice {
  constructor() {
    this.enabled = false;
    this.last = 0;
    this.ctx = null;
  }

  line(agent, kind) {
    const u = UNIT_LINES[agent?.id] || {};
    const f = FACTION_LINES[agent?.faction] || FACTION_LINES.neutral;
    return pick(u[kind] || u.any || f[kind] || FACTION_LINES.neutral[kind] || [""]);
  }

  toggle() {
    this.enabled = !this.enabled;
    if (this.enabled && !this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    return this.enabled;
  }

  // Say a line out loud (throttled so the floor doesn't turn into noise).
  speak(agent, text, force = false) {
    if (!this.enabled || !text || !("speechSynthesis" in window)) return;
    const now = performance.now();
    if (!force && now - this.last < 2200) return;
    this.last = now;
    const v = VOICE[agent?.faction] || VOICE.neutral;
    const u = new SpeechSynthesisUtterance(text);
    u.pitch = v.pitch; u.rate = v.rate; u.volume = 0.9;
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  }

  blip(kind = "tick") {
    if (!this.enabled || !this.ctx) return;
    const ctx = this.ctx, t = ctx.currentTime;
    const tones = { tick: [880, 0.05], select: [660, 0.08], trade: [523, 0.12, 784], alert: [220, 0.25, 180], win: [523, 0.15, 659, 784] };
    const [f0, dur, ...more] = tones[kind] || tones.tick;
    [f0, ...more].forEach((f, i) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = kind === "alert" ? "sawtooth" : "square";
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t + i * dur);
      g.gain.exponentialRampToValueAtTime(0.06, t + i * dur + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, t + (i + 1) * dur);
      o.connect(g).connect(ctx.destination);
      o.start(t + i * dur); o.stop(t + (i + 1) * dur + 0.02);
    });
  }
}
