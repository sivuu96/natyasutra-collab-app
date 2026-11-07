import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { LoginPage } from "../pages/LoginPage";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("LoginPage", () => {
  it("shows the server's generic error for a failed login", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/v1/auth/me") return jsonResponse(401, { detail: "Not authenticated" });
      return jsonResponse(401, { detail: "Invalid email or password" });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AuthProvider>
        <MemoryRouter>
          <LoginPage />
        </MemoryRouter>
      </AuthProvider>,
    );

    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "nobody@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password-value");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Invalid email or password"));
    const loginCall = fetchMock.mock.calls.find(([url]) => url === "/api/v1/auth/login");
    expect(loginCall).toBeDefined();
  });
});
