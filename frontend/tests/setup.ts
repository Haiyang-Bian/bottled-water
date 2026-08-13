import "@testing-library/jest-dom/vitest";

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length(): number {
    return this.values.size;
  }

  clear(): void {
    this.values.clear();
  }

  getItem(key: string): string | null {
    return this.values.get(String(key)) ?? null;
  }

  key(index: number): string | null {
    return [...this.values.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.values.delete(String(key));
  }

  setItem(key: string, value: string): void {
    this.values.set(String(key), String(value));
  }
}

const localStorageMock = new MemoryStorage();
const sessionStorageMock = new MemoryStorage();
Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: localStorageMock
});
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: localStorageMock
});
Object.defineProperty(window, "sessionStorage", {
  configurable: true,
  value: sessionStorageMock
});
Object.defineProperty(globalThis, "sessionStorage", {
  configurable: true,
  value: sessionStorageMock
});

beforeEach(() => {
  localStorageMock.clear();
  sessionStorageMock.clear();
});

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false
  })
});

const jsdomGetComputedStyle = window.getComputedStyle.bind(window);

Object.defineProperty(window, "getComputedStyle", {
  writable: true,
  value: (element: Element) => jsdomGetComputedStyle(element)
});
