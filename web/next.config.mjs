/** Static export: FastAPI serves web/out, there is no Node process in production. */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
};

// `next dev` talks to a local `mas serve` (http://127.0.0.1:7070).
if (process.env.NODE_ENV === "development") {
  nextConfig.rewrites = async () => [{ source: "/api/:path*", destination: "http://127.0.0.1:7070/api/:path*" }];
}

export default nextConfig;
