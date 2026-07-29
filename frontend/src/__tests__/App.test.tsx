import { beforeEach, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import App from "../App";

function renderApp(path = "/") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
});

test("shows the operator dashboard with a development project when the backend is unavailable", async () => {
  renderApp();

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "新建出图项目" })).toBeInTheDocument();
  expect(screen.getByText("夏日家居上新")).toBeInTheDocument();
});

test("creates a development project from the project form and opens asset grouping", async () => {
  const user = userEvent.setup();
  renderApp("/projects/new");

  await user.clear(screen.getByLabelText("项目名称"));
  await user.type(screen.getByLabelText("项目名称"), "秋季新品");
  await user.click(screen.getByRole("button", { name: "创建项目并上传素材" }));

  expect(await screen.findByRole("heading", { name: "秋季新品" })).toBeInTheDocument();
  expect(screen.getByText("上传商品素材")).toBeInTheDocument();
});

test("shows a product brief and output slots in the studio", async () => {
  renderApp("/projects/project-demo/studio/sku-lamp");

  expect(await screen.findByRole("heading", { name: "商品创作台" })).toBeInTheDocument();
  expect(screen.getByLabelText("商品卖点与规格")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "主图" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存 Brief" })).toBeInTheDocument();
});

test("lets an operator select a review result and request a modification", async () => {
  const user = userEvent.setup();
  renderApp("/review");

  expect(await screen.findByRole("heading", { name: "审核中心" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "请求修改" }));

  expect(screen.getByText("已标记为需要修改")).toBeInTheDocument();
});
