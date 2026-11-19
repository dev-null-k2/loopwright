// THE PLAYER: turns a list of events into something you can watch.
//
// A turn finishes in milliseconds, far faster than anyone can follow. So the
// cockpit never shows events as they arrive. Live or recorded, events go into
// one list and this player walks it at a readable pace:
//
//   live turn   events are appended as the server streams them; the player
//               keeps playing and simply waits at the end for the next one
//   replay      the whole list is loaded from a trace file; same controls
//
// Controls: play/pause, step back and forward, jump to either end, scrub,
// and a speed multiplier. Nothing here re-runs the model; a replay shows the
// record of what happened.

const BASE_MS = 900;           // time on each step at 1x

export class Player {
  constructor(onChange) {
    this.onChange = onChange;  // (state) => void, called whenever anything visible changes
    this.all = [];             // every event of the run
    this.events = [];          // the events being shown (worker steps may be filtered out)
    this.index = -1;
    this.playing = false;
    this.live = false;         // more events may still arrive
    this.speed = 1;
    this.onlyMain = false;
    this.meta = {};
    this._timer = null;
  }

  // Load a finished run (replay) or start an empty live one.
  load(events, meta = {}, { live = false, autoplay = true } = {}) {
    clearTimeout(this._timer);
    this.all = events.slice();
    this.meta = meta;
    this.live = live;
    this._filter();
    this.index = -1;
    this.playing = false;
    this._emit(false);
    if (autoplay) this.play();
  }

  // A live event arrived.
  push(event) {
    this.all.push(event);
    if (this.onlyMain && event.span !== "main") return;
    this.events.push(event);
    if (this.playing && this._waiting) this._tick(true);
    else this._emit(false);
  }

  // The live turn is over; nothing more will arrive.
  finish() {
    this.live = false;
    if (this.playing && this.index >= this.events.length - 1) this.playing = false;
    this._emit(false);
  }

  play() {
    if (!this.events.length && !this.live) return;
    if (!this.live && this.index >= this.events.length - 1) this.index = -1;    // play again from the top
    this.playing = true;
    this._tick(true);
  }

  pause() {
    this.playing = false;
    clearTimeout(this._timer);
    this._emit(false);
  }

  toggle() { this.playing ? this.pause() : this.play(); }

  step(delta) {
    this.pause();
    this.seek(this.index + delta, delta > 0);
  }

  seek(index, animate = false) {
    clearTimeout(this._timer);
    this.index = Math.max(-1, Math.min(this.events.length - 1, index));
    this._emit(animate);
  }

  // Jump straight to a particular event (used when an approval is waiting).
  seekToSeq(seq) {
    const at = this.events.findIndex((e) => e.seq === seq);
    if (at !== -1) { this.playing = false; this.seek(at, true); }
  }

  setSpeed(speed) { this.speed = speed; }

  setOnlyMain(flag) {
    const current = this.events[this.index];
    this.onlyMain = flag;
    this._filter();
    this.index = current ? Math.max(-1, this.events.findLastIndex((e) => e.seq <= current.seq)) : -1;
    this._emit(false);
  }

  get current() { return this.events[this.index] || null; }

  _filter() { this.events = this.onlyMain ? this.all.filter((e) => e.span === "main") : this.all.slice(); }

  _tick(immediate) {
    clearTimeout(this._timer);
    this._waiting = false;
    if (!this.playing) return;
    if (this.index >= this.events.length - 1) {
      if (this.live) { this._waiting = true; this._emit(false); return; }     // wait for the next live event
      this.playing = false;
      this._emit(false);
      return;
    }
    void immediate;
    const advance = () => {
      this.index += 1;
      this._emit(true);
      this._timer = setTimeout(() => this._tick(false), this._dwell(this.events[this.index]));
    };
    advance();
  }

  // Steps that carry more to read get a little longer.
  _dwell(event) {
    const weight = event.status === "ok" || event.status === "skip" ? 1 : 1.5;
    return Math.max(110, (BASE_MS * weight) / this.speed);
  }

  _emit(animate) {
    this.onChange({ events: this.events, index: this.index, current: this.current, playing: this.playing,
                    live: this.live, waiting: !!this._waiting, speed: this.speed, animate, meta: this.meta,
                    total: this.all.length });
  }
}
