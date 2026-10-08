import "@testing-library/jest-dom/vitest";

// jsdom does not implement matchMedia. Mantine components use it for color-scheme checks.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

// Mantine restores scroll position when a Drawer closes; jsdom has no scrollTo.
Object.defineProperty(window, "scrollTo", {
  writable: true,
  value: () => undefined,
});

// jsdom test double for Mantine components that observe layout changes.
class ResizeObserverTestDouble implements ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(window, "ResizeObserver", {
  writable: true,
  value: ResizeObserverTestDouble,
});
Object.defineProperty(globalThis, "ResizeObserver", {
  writable: true,
  value: ResizeObserverTestDouble,
});
