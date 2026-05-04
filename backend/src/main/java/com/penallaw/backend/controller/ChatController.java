package com.penallaw.backend.controller;

import com.penallaw.backend.dto.ChatDTOs;
import com.penallaw.backend.service.ChatService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.web.bind.annotation.*;

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
}
