package com.penallaw.backend.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public class AuthDTOs {

        public record RegisterRequest(
                        @NotBlank @Email String email,
                        @NotBlank @Size(min = 8, message = "Password must be at least 8 characters") String password,
                        String fullName) {
        }

        public record LoginRequest(
                        @NotBlank @Email String email,
                        @NotBlank String password) {
        }

        public record AuthResponse(
                        String token,
                        String email,
                        String fullName,
                        String role) {
        }
}
