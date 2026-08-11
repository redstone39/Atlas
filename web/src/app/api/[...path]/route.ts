export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export {
  proxyApiRequest as DELETE,
  proxyApiRequest as GET,
  proxyApiRequest as HEAD,
  proxyApiRequest as PATCH,
  proxyApiRequest as POST,
  proxyApiRequest as PUT,
} from "@/server/api-proxy";
