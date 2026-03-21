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

    /** Create a new chat session */
    @PostMapping("/sessions")
    public ResponseEntity<ChatDTOs.SessionResponse> createSession(
            @AuthenticationPrincipal UserDetails userDetails,
            @RequestBody(required = false) ChatDTOs.CreateSessionRequest request
    ) {
        if (request == null) request = new ChatDTOs.CreateSessionRequest("neutral");
        return ResponseEntity.ok(chatService.createSession(userDetails.getUsername(), request));
    }

    /** List all sessions for current user */
    @GetMapping("/sessions")
    public ResponseEntity<List<ChatDTOs.SessionResponse>> getSessions(
            @AuthenticationPrincipal UserDetails userDetails
    ) {
        return ResponseEntity.ok(chatService.getUserSessions(userDetails.getUsername()));
    }

    /** Send a message in a session — calls AI service and stores response */
    @PostMapping("/sessions/{sessionId}/messages")
    public ResponseEntity<ChatDTOs.MessageResponse> sendMessage(
            @AuthenticationPrincipal UserDetails userDetails,
            @PathVariable UUID sessionId,
            @Valid @RequestBody ChatDTOs.SendMessageRequest request
    ) {
        return ResponseEntity.ok(chatService.sendMessage(userDetails.getUsername(), sessionId, request));
    }

    /** Get full conversation history of a session */
    @GetMapping("/sessions/{sessionId}/messages")
    public ResponseEntity<ChatDTOs.ConversationHistoryResponse> getHistory(
            @AuthenticationPrincipal UserDetails userDetails,
            @PathVariable UUID sessionId
    ) {
        return ResponseEntity.ok(chatService.getHistory(userDetails.getUsername(), sessionId));
    }
}
