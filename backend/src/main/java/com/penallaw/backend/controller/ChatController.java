package com.penallaw.backend.controller;

import com.penallaw.backend.dto.ChatDTOs;
import com.penallaw.backend.service.ChatService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.web.bind.annotation.*;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;

@RestController
@RequestMapping("/api/chat")
@RequiredArgsConstructor
public class ChatController {

    private final ChatService chatService;

    // ── GUEST SESSIONS (no login required) ──────────────────────

    @PostMapping("/guest/{guestId}/sessions")
    public ResponseEntity<ChatDTOs.SessionResponse> createGuestSession(
            @PathVariable String guestId,
            @RequestBody(required = false) ChatDTOs.CreateSessionRequest request
    ) {
        return ResponseEntity.ok(chatService.createGuestSession(guestId, request));
    }

    @GetMapping("/guest/{guestId}/sessions")
    public ResponseEntity<List<ChatDTOs.SessionResponse>> getGuestSessions(
            @PathVariable String guestId
    ) {
        return ResponseEntity.ok(chatService.getGuestSessions(guestId));
    }

    // ── AUTHENTICATED SESSIONS ───────────────────────────────────

    @PostMapping("/sessions")
    public ResponseEntity<ChatDTOs.SessionResponse> createSession(
            @AuthenticationPrincipal UserDetails userDetails,
            @RequestBody(required = false) ChatDTOs.CreateSessionRequest request
    ) {
        if (request == null) request = new ChatDTOs.CreateSessionRequest("neutral", null);
        return ResponseEntity.ok(chatService.createSession(userDetails.getUsername(), request));
    }

    @GetMapping("/sessions")
    public ResponseEntity<List<ChatDTOs.SessionResponse>> getSessions(
            @AuthenticationPrincipal UserDetails userDetails
    ) {
        return ResponseEntity.ok(chatService.getUserSessions(userDetails.getUsername()));
    }

    // ── MESSAGES (works for both guest and auth sessions by sessionId) ──

    @PostMapping("/sessions/{sessionId}/messages")
    public ResponseEntity<ChatDTOs.MessageResponse> sendMessage(
            @PathVariable UUID sessionId,
            @Valid @RequestBody ChatDTOs.SendMessageRequest request
    ) {
        return ResponseEntity.ok(chatService.sendMessage(sessionId, request));
    }

    @GetMapping("/sessions/{sessionId}/messages")
    public ResponseEntity<ChatDTOs.ConversationHistoryResponse> getHistory(
            @PathVariable UUID sessionId
    ) {
        return ResponseEntity.ok(chatService.getHistory(sessionId));
    }

    @DeleteMapping("/sessions/{sessionId}")
    public ResponseEntity<ChatDTOs.DeleteSessionResponse> deleteSession(
            @PathVariable UUID sessionId
    ) {
        chatService.deleteSession(sessionId);
        return ResponseEntity.ok(new ChatDTOs.DeleteSessionResponse(sessionId, "Session deleted"));
    }

    // ── CSV EXPORT ───────────────────────────────────────────────

    /**
     * Download the full conversation log as a UTF-8 CSV file.
     * Accessible without authentication — gated by the unguessable session UUID,
     * consistent with the other per-session endpoints (send / get messages, delete).
     *
     * GET /api/chat/sessions/{sessionId}/export.csv
     */
    @GetMapping("/sessions/{sessionId}/export.csv")
    public ResponseEntity<byte[]> exportSessionCsv(
            @PathVariable UUID sessionId
    ) {
        byte[] csvBytes = chatService.exportSessionAsCsv(sessionId);

        // Build a safe ASCII filename from the session ID prefix;
        // the Content-Disposition header below carries the user-visible name.
        String filename = "session-" + sessionId.toString().substring(0, 8) + ".csv";

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(new MediaType("text", "csv", StandardCharsets.UTF_8));
        headers.setContentDisposition(
                ContentDisposition.attachment().filename(filename, StandardCharsets.UTF_8).build()
        );
        headers.setContentLength(csvBytes.length);

        return ResponseEntity.ok()
                .headers(headers)
                .body(csvBytes);
    }
}
