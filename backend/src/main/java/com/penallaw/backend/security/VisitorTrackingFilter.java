package com.penallaw.backend.security;

import com.penallaw.backend.service.VisitorTrackingService;
import jakarta.servlet.*;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.io.IOException;

/**
 * Intercepts every incoming HTTP request and increments the visitor counter.
 * Fast, low-memory middleware approach.
 */
@Component
@RequiredArgsConstructor
public class VisitorTrackingFilter implements Filter {

    private final VisitorTrackingService trackingService;

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        
        if (request instanceof HttpServletRequest httpRequest) {
            String path = httpRequest.getRequestURI();
            // Optional: Skip tracking for health checks or specific internal endpoints
            if (!path.startsWith("/actuator") && !path.contains("favicon")) {
                trackingService.increment();
            }
        }
        
        chain.doFilter(request, response);
    }
}
