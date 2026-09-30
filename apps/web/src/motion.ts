export const fastTransition = {
  duration: 0.16,
  ease: [0.22, 1, 0.36, 1],
} as const;

export const standardTransition = {
  duration: 0.2,
  ease: [0.22, 1, 0.36, 1],
} as const;

export const rowVariants = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -4 },
} as const;

export const detailVariants = {
  hidden: { opacity: 0, height: 0, y: 4 },
  visible: { opacity: 1, height: "auto", y: 0 },
  exit: { opacity: 0, height: 0, y: -4 },
} as const;

export function transitionFor(reducedMotion: boolean) {
  return reducedMotion ? ({ duration: 0 } as const) : standardTransition;
}
