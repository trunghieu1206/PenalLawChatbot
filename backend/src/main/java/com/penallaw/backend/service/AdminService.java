package com.penallaw.backend.service;

import com.penallaw.backend.dto.AdminDTOs;
import com.penallaw.backend.entity.ChatSession;
import com.penallaw.backend.entity.Feedback;
import com.penallaw.backend.entity.User;
import com.penallaw.backend.repository.ChatMessageRepository;
import com.penallaw.backend.repository.ChatSessionRepository;
import com.penallaw.backend.repository.FeedbackRepository;
import com.penallaw.backend.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * Service responsible for admin-only operations.
 *
 * Responsibilities:
 *   - Retrieve and review user feedback on AI responses.
 *   - Aggregate per-user case (session) statistics for the admin panel.
 *   - Submit and update feedback review status.
 *
 * Public-facing stats (for the /api/home endpoint) have been moved to StatsService.
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class AdminService {

    private final ChatSessionRepository sessionRepository;
    private final ChatMessageRepository messageRepository;
    private final FeedbackRepository feedbackRepository;
    private final UserRepository userRepository;

    // ── FEEDBACK ADMIN LIST ───────────────────────────────────────

    @Transactional(readOnly = true)
    public List<AdminDTOs.FeedbackDetail> getAllFeedback() {
        List<Feedback> feedbacks = feedbackRepository.findAllByOrderByCreatedAtDesc();
        return feedbacks.stream().map(f -> {
            UUID sid = f.getSessionId();

            // Load session conversation for context (skip gracefully if sessionId is missing)
            List<AdminDTOs.MessageSummary> conversation = List.of();
            if (sid != null) {
                conversation = messageRepository.findBySessionIdOrderByCreatedAtAsc(sid)
                        .stream()
                        .map(m -> new AdminDTOs.MessageSummary(
                                m.getId(), m.getRole(), m.getContent(), m.getCreatedAt()))
                        .collect(Collectors.toList());
            }

            // Session metadata
            String sessionMode = (sid != null)
                    ? sessionRepository.findById(sid).map(ChatSession::getMode).orElse("unknown")
                    : "unknown";

            return new AdminDTOs.FeedbackDetail(
                    f.getId(), sid, f.getMessageId(),
                    f.getIsCorrect(), f.getComment(),
                    f.getStatus() != null ? f.getStatus() : "can_xem_xet",
                    f.getCreatedAt(),
                    sessionMode, conversation
            );
        }).collect(Collectors.toList());
    }

    // ── USER CASE STATS ───────────────────────────────────────────

    @Transactional(readOnly = true)
    public List<AdminDTOs.UserCaseStat> getUserCaseStats() {
        LocalDateTime startOfToday = LocalDateTime.of(LocalDate.now(), LocalTime.MIDNIGHT);

        // All-time totals per user
        List<Object[]> totals = sessionRepository.findUserSessionCounts();

        // Today's totals per user
        Map<UUID, Long> todayMap = sessionRepository.findUserSessionCountsToday(startOfToday)
                .stream()
                .collect(Collectors.toMap(
                        row -> ((User) row[0]).getId(),
                        row -> (Long) row[1]
                ));

        return totals.stream()
                .map(row -> {
                    User u = (User) row[0];
                    long total = (Long) row[1];
                    long today = todayMap.getOrDefault(u.getId(), 0L);
                    return new AdminDTOs.UserCaseStat(
                            u.getId(), u.getEmail(), u.getFullName(), u.getRole(), total, today);
                })
                .sorted(Comparator.comparingLong(AdminDTOs.UserCaseStat::totalCases).reversed())
                .collect(Collectors.toList());
    }

    // ── SUBMIT FEEDBACK ───────────────────────────────────────────

    @Transactional
    public AdminDTOs.FeedbackResponse submitFeedback(UUID sessionId, UUID messageId,
                                                      boolean isCorrect, String comment) {
        // Upsert: update existing feedback for this message, or create a new record
        Feedback feedback = feedbackRepository.findByMessageId(messageId)
                .orElse(Feedback.builder().sessionId(sessionId).messageId(messageId).build());

        feedback.setIsCorrect(isCorrect);
        feedback.setComment(comment);
        feedback = feedbackRepository.save(feedback);
        return new AdminDTOs.FeedbackResponse(feedback.getId(), "Đã ghi nhận phản hồi. Cảm ơn bạn!");
    }

    // ── UPDATE FEEDBACK STATUS ────────────────────────────────────

    @Transactional
    public AdminDTOs.FeedbackResponse updateFeedbackStatus(UUID feedbackId, String newStatus) {
        if (!"can_xem_xet".equals(newStatus) && !"da_xem_xet".equals(newStatus)) {
            throw new IllegalArgumentException("Trạng thái không hợp lệ: " + newStatus);
        }
        Feedback feedback = feedbackRepository.findById(feedbackId)
                .orElseThrow(() -> new RuntimeException("Feedback not found: " + feedbackId));
        feedback.setStatus(newStatus);
        feedbackRepository.save(feedback);
        return new AdminDTOs.FeedbackResponse(feedbackId, "Đã cập nhật trạng thái.");
    }

}
