import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIE = "anvil_session";
// "/" is the public landing page; auth pages redirect away when signed in.
const PUBLIC_PATHS = ["/login", "/signup"];

/**
 * Cookie-presence gate. The cookie is signed and validated by the API on every
 * call — this only decides which shell to show, so a stale cookie just means
 * one 401 and a client-side bounce to /login.
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = request.cookies.has(SESSION_COOKIE);
  const isLanding = pathname === "/";
  const isAuthPage = PUBLIC_PATHS.some((p) => pathname.startsWith(p));

  if (!hasSession && !isLanding && !isAuthPage) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = pathname === "/home" ? "" : `?next=${encodeURIComponent(pathname)}`;
    return NextResponse.redirect(url);
  }

  // Signed-in users skip the landing and auth pages straight into the app.
  if (hasSession && (isLanding || isAuthPage)) {
    const url = request.nextUrl.clone();
    url.pathname = "/home";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Everything except the API proxy, Next internals, and static assets.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
