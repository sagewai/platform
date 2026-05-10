// Allow side-effect CSS imports in TypeScript (Next.js processes them via PostCSS).
declare module '*.css' {
  const _: Record<string, string>;
  export default _;
}
