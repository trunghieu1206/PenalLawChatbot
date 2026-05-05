package com.penallaw.backend.entity;

import com.fasterxml.jackson.annotation.JsonFormat;
import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;
import java.util.UUID;

@Entity
@Table(name = "feedbacks")
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class Feedback {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    /** The session this feedback belongs to (nullable for legacy records). */
    @Column(name = "session_id")
    private UUID sessionId;

    /** The specific AI message being rated (nullable — covers whole session if null). */
    @Column(name = "message_id")
    private UUID messageId;

    /** true = correct / helpful, false = incorrect / unhelpful. */
    @Column(name = "is_correct", nullable = false)
    private Boolean isCorrect;

    /** Optional free-text comment explaining the error. */
    @Column(columnDefinition = "TEXT")
    private String comment;

    /**
     * Review status: "can_xem_xet" (needs review) or "da_xem_xet" (reviewed).
     * Defaults to "can_xem_xet" both in Java and at the database level.
     */
    @Builder.Default
    @Column(length = 30, nullable = false, columnDefinition = "VARCHAR(30) DEFAULT 'can_xem_xet'")
    private String status = "can_xem_xet";

    @CreationTimestamp
    @Column(name = "created_at", updatable = false, nullable = false)
    @JsonFormat(pattern = "yyyy-MM-dd'T'HH:mm:ss[.SSS]'Z'", timezone = "UTC")
    private LocalDateTime createdAt;
}
