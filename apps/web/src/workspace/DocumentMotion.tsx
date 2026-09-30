import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { useRef, useState } from "react";
import "./document-motion.css";

gsap.registerPlugin(useGSAP);

/** A user-triggered illustration. Never represents a live job or measured progress. */
export function DocumentMotion() {
  const root = useRef<HTMLDivElement>(null);
  const timeline = useRef<gsap.core.Timeline | null>(null);
  const running = useRef(false);
  const [busy, setBusy] = useState(false);
  const [played, setPlayed] = useState(false);
  const [message, setMessage] = useState("From paper to structured data.");
  const { contextSafe } = useGSAP({ scope: root });

  const play = contextSafe(() => {
    if (running.current || !root.current) return;
    running.current = true;
    setBusy(true);
    timeline.current?.kill();
    const node = root.current;
    const original = node.querySelector(".dm-original");
    const backs = node.querySelectorAll(".dm-back");
    const output = node.querySelector(".dm-output");
    const scan = node.querySelector(".dm-scan");
    const cells = node.querySelectorAll(".dm-cell");
    const wide = (node.querySelector(".dm-stage")?.clientWidth ?? 0) >= 500;
    const spread = wide ? 130 : 0;
    const duration = matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 1;
    gsap.killTweensOf(backs);
    gsap.set([original, ...backs], { x: 0, y: 0, opacity: 1 });
    gsap.set(backs[0], { rotation: -8 });
    gsap.set(backs[1], { rotation: 6 });
    gsap.set(original, { rotation: 0 });
    gsap.set(output, { x: spread, y: 0, opacity: 0, rotation: 0 });
    gsap.set(scan, { y: 0, opacity: 0 });
    timeline.current = gsap.timeline({
      defaults: { ease: "power2.inOut" },
      onComplete: () => {
        running.current = false;
        setBusy(false);
        setPlayed(true);
        setMessage("Illustration complete. Original preserved.");
      },
    })
      .call(() => setMessage("A document comes into focus"))
      .to(backs[0], { rotation: -15, x: -16, duration: .4 * duration }, 0)
      .to(backs[1], { rotation: 12, x: 14, duration: .4 * duration }, 0)
      .to(original, { y: -10, rotation: -2, duration: .4 * duration }, 0)
      .to(backs, { opacity: 0, y: 15, duration: .3 * duration })
      .call(() => setMessage("Finding the structure"))
      .set(scan, { opacity: 1 })
      .to(scan, { y: 235, duration: 1 * duration, ease: "none" })
      .set(scan, { opacity: 0 })
      .call(() => setMessage("Bringing the table into view"))
      .to(original, { x: -spread, y: 0, rotation: wide ? -5 : 0, opacity: wide ? 1 : 0, duration: .6 * duration })
      .to(output, { opacity: 1, rotation: wide ? 3 : 0, duration: .4 * duration }, "<")
      .fromTo(cells, { x: wide ? -180 : 0, y: wide ? 15 : 30, opacity: 0, scale: .75 },
        { x: 0, y: 0, opacity: 1, scale: 1, duration: .55 * duration, stagger: .04 * duration, ease: "power3.out" });
  });

  const fan = contextSafe((expanded: boolean) => {
    if (running.current || played || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const backs = root.current?.querySelectorAll(".dm-back");
    if (!backs) return;
    gsap.to(backs[0], { rotation: expanded ? -13 : -8, x: expanded ? -8 : 0, duration: .25, overwrite: "auto" });
    gsap.to(backs[1], { rotation: expanded ? 11 : 6, x: expanded ? 8 : 0, duration: .25, overwrite: "auto" });
  });

  return (
    <div className="document-motion" ref={root}>
      <header className="dm-heading"><h2>How Parserium works</h2><p>Illustration only. No live processing.</p></header>
      <div className="dm-stage" aria-hidden="true" onPointerEnter={() => fan(true)} onPointerLeave={() => fan(false)}>
        <div className="dm-sheet dm-back dm-back-one" />
        <div className="dm-sheet dm-back dm-back-two" />
        <div className="dm-sheet dm-original">
          <div className="dm-tag"><span>Original</span><span>PDF</span></div>
          <div className="dm-title">Source document</div>
          <div className="dm-line" /><div className="dm-line dm-short" />
          <div className="dm-table">{Array.from({ length: 12 }, (_, i) => <i key={i} />)}</div>
          <div className="dm-scan" />
        </div>
        <div className="dm-sheet dm-output">
          <div className="dm-tag"><span>Structured</span><span>Table</span></div>
          <div className="dm-grid">{Array.from({ length: 12 }, (_, i) => <i className="dm-cell" key={i} />)}</div>
          <p className="dm-caption">Source preserved. Structure revealed.</p>
        </div>
      </div>
      <footer className="dm-footer"><span role="status">{message}</span><button type="button" onClick={play} disabled={busy}>{busy ? "Playing demo" : played ? "Replay demo" : "Play demo"}</button></footer>
    </div>
  );
}
