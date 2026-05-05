package com.penallaw.backend.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.*;

@Entity
@Table(name = "site_stats")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class SiteStats {

    @Id
    private String id; // We'll use "global" as the single row ID

    private long visitorCount;
}
