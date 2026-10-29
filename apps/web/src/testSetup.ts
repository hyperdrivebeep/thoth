// The screen language follows the browser when the person has not chosen one. The tests are written
// for a Korean browser; a test that needs another browser language sets navigator.languages itself.
Object.defineProperty(globalThis.navigator, "languages", { value: ["ko-KR", "ko"], configurable: true });
Object.defineProperty(globalThis.navigator, "language", { value: "ko-KR", configurable: true });
