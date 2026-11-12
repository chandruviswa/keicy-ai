import { Lock, SearchX } from "lucide-react";
import type { ReactNode } from "react";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import Login from "./pages/Login";
import { ToastProvider } from "./toast";
import { Layout } from "./components/Layout";
import { Card, EmptyState } from "./components/ui";
import Dashboard from "./pages/Dashboard";
import Approvals from "./pages/Approvals";
import Audit from "./pages/Audit";
import Payments from "./pages/Payments";
import PurchaseOrders from "./pages/PurchaseOrders";
import RequestDetail from "./pages/RequestDetail";
import Vendors from "./pages/Vendors";
import Workspace from "./pages/Workspace";

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Gate />
      </ToastProvider>
    </AuthProvider>
  );
}

function Gate() {
  const { user, ready } = useAuth();
  if (!ready) return null;
  if (!user) return <Login />;
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="workspace" element={<Workspace />} />
          <Route path="workspace/:id" element={<RequestDetail />} />
          <Route path="vendors" element={<Vendors />} />
          <Route path="purchase-orders" element={<PurchaseOrders />} />
          <Route path="approvals" element={<RequireRole roles={["approver", "admin"]}><Approvals /></RequireRole>} />
          <Route path="payments" element={<Payments />} />
          <Route path="audit" element={<RequireRole roles={["approver", "admin"]}><Audit /></RequireRole>} />
          <Route
            path="*"
            element={
              <Card>
                <EmptyState icon={SearchX} title="Page not found" description="That page doesn't exist." />
                <p className="pb-8 text-center text-sm">
                  <Link to="/" className="font-medium text-brand-600 hover:text-brand-700">
                    Back to overview
                  </Link>
                </p>
              </Card>
            }
          />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

function RequireRole({ roles, children }: { roles: ("approver" | "admin" | "requester")[]; children: ReactNode }) {
  const { user } = useAuth();
  if (user && roles.includes(user.role)) return <>{children}</>;
  return (
    <Card>
      <EmptyState icon={Lock} title="Not available for your role" description="This screen is for approvers and administrators." />
    </Card>
  );
}
