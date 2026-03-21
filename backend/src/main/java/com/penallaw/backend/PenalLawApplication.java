package com.penallaw.backend;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cache.annotation.EnableCaching;

@SpringBootApplication
@EnableCaching
public class PenalLawApplication {
    public static void main(String[] args) {
        SpringApplication.run(PenalLawApplication.class, args);
    }
}
