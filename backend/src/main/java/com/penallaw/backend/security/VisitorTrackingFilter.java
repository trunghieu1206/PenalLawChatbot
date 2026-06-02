package com.penallaw.backend.security;

import jakarta.servlet.*;
import jakarta.servlet.http.HttpServletRequest;

import java.io.IOException;

/**
 * VisitorTrackingFilter — DISABLED.
 *
 * The old implementation counted every HTTP request, which caused one user
 * navigating between pages to be counted multiple times.
 *
 * Visitor tracking is now handled by a dedicated endpoint:
 *   POST /api/home/track-visit  { visitor_id: "<browser-uuid>" }
 *
 * The browser sends this once per day (checked via localStorage date).
 * The backend stores one row per (visitor_id, date) in the visitor_logs table,
 * enforced by a DB unique constraint — so the count is always accurate.
 *
 * This class is intentionally NOT annotated with @Component and will NOT
 * be registered as a servlet filter.
 */
public class VisitorTrackingFilter implements Filter {

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        chain.doFilter(request, response); // pass through — no tracking here
    }
}
