package com.penallaw.backend.entity;

import com.penallaw.backend.converter.JsonListConverter;
import com.penallaw.backend.converter.JsonMapConverter;
import com.fasterxml.jackson.annotation.JsonFormat;
import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Entity
@Table(name = "chat_messages")
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ChatMessage {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "session_id", nullable = false)
    private ChatSession session;

    @Column(length = 10, nullable = false)
    private String role; // "user" or "assistant"

    @Column(columnDefinition = "TEXT", nullable = false)
    private String content;

    // Use @Convert to avoid Hibernate 6.6 ClassCastException with PostgreSQL JSONB
    @Convert(converter = JsonMapConverter.class)
    @Column(name = "extracted_facts", columnDefinition = "TEXT")
    private Map<String, Object> extractedFacts;

    // Use @Convert instead of @JdbcTypeCode(SqlTypes.JSON) to avoid
    // Hibernate 6.6 bug: ClassCastException ArrayList→String in AbstractJsonFormatMapper
    @Convert(converter = JsonListConverter.class)
    @Column(name = "mapped_laws", columnDefinition = "TEXT")
    private List<Map<String, Object>> mappedLaws;

    @CreationTimestamp
    @Column(name = "created_at", updatable = false, nullable = false, columnDefinition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    @JsonFormat(pattern = "yyyy-MM-dd'T'HH:mm:ss[.SSS]'Z'", timezone = "UTC")
    private LocalDateTime createdAt;
}
