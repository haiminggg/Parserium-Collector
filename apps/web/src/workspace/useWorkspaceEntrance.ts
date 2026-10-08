import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import type { RefObject } from "react";

gsap.registerPlugin(useGSAP);

export function useWorkspaceEntrance(scope: RefObject<HTMLDivElement | null>) {
  useGSAP(() => {
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      const targets = scope.current?.querySelectorAll("[data-workspace-enter]");
      if (!targets?.length) return;
      gsap.from(targets, {
        y: 10,
        opacity: 0,
        duration: 0.4,
        stagger: 0.05,
        ease: "power2.out",
        clearProps: "transform,opacity",
      });
    }, scope);
    return () => media.revert();
  }, { scope });
}
