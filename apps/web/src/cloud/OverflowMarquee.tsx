import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";

interface OverflowMarqueeProps {
  text: string;
}

export function OverflowMarquee({ text }: OverflowMarqueeProps) {
  const viewportRef = useRef<HTMLSpanElement>(null);
  const contentRef = useRef<HTMLSpanElement>(null);
  const [distance, setDistance] = useState(0);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    const content = contentRef.current;
    if (!viewport || !content) return;

    let disposed = false;
    const measure = () => {
      if (disposed) return;
      const nextDistance = Math.max(0, Math.ceil(content.scrollWidth - viewport.clientWidth));
      setDistance(current => current === nextDistance ? current : nextDistance);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(viewport);
    observer.observe(content);
    measure();
    void document.fonts?.ready.then(measure);

    return () => {
      disposed = true;
      observer.disconnect();
    };
  }, [text]);

  const overflows = distance > 1;
  const motionStyle = overflows ? {
    "--cloud-overflow-distance": `${distance}px`,
    "--cloud-overflow-duration": `${Math.min(18, Math.max(7, 6 + distance / 15)).toFixed(2)}s`,
  } as CSSProperties : undefined;

  return <span className="cloud-overflow-marquee" data-overflow={overflows ? "true" : "false"} title={text}>
    <span ref={viewportRef} className="cloud-overflow-marquee-viewport">
      <span ref={contentRef} className="cloud-overflow-marquee-content" style={motionStyle}>{text}</span>
    </span>
  </span>;
}
