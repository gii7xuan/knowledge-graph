window.mathJaxLoadFailed = false;
window.mathJaxReady = new Promise(resolve => {
  window.resolveMathJaxReady = resolve;
});
window.MathJax = {
  tex: {
    inlineMath: [["$", "$"], ["\\(", "\\)"]],
    displayMath: [["$$", "$$"], ["\\[", "\\]"]],
    processEscapes: true
  },
  options: {
    skipHtmlTags: ["script", "noscript", "style", "textarea", "pre", "code"]
  },
  startup: {
    ready: () => {
      window.MathJax.startup.defaultReady();
      window.resolveMathJaxReady();
      if (window.onMathJaxReady) window.onMathJaxReady();
    }
  }
};
