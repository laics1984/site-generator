/// <reference types="vite/client" />

// `?raw` imports the file's text instead of injecting it as a stylesheet.
// The preview needs preview.css as a string so it can inject it into the
// preview iframe's document rather than this app's — see preview/PreviewFrame.
declare module '*.css?raw' {
  const content: string
  export default content
}
