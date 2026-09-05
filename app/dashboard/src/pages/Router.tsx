import { Navigate, createHashRouter } from "react-router-dom";
import { ReactNode } from "react";
import useGetUser from "hooks/useGetUser";
import { fetch } from "../service/http";
import { getAuthToken } from "../utils/authStorage";
import { Dashboard } from "./Dashboard";
import { Admins } from "./Admins";
import { Login } from "./Login";
import { AuditLogs } from "./AuditLogs";
import { DeviceLimits } from "./DeviceLimits";
import { Plans } from "./Plans";
import { Settings } from "./Settings";
const fetchAdminLoader = () => {
    return fetch("/admin", {
        headers: {
            Authorization: `Bearer ${getAuthToken()}`,
        },
    });
};
const OwnerOnly = ({ children }: { children: ReactNode }) => {
    const { userData, getUserIsPending } = useGetUser();
    if (getUserIsPending) return null;
    return userData.is_sudo || userData.role === "OWNER" ? <>{children}</> : <Navigate to="/" replace />;
};
export const router = createHashRouter([
    {
        path: "/",
        element: <Dashboard />,
        errorElement: <Login />,
        loader: fetchAdminLoader,
    },
    {
        path: "/login/",
        element: <Login />,
    },
    {
        path: "/admins/",
        element: <Admins />,
        errorElement: <Login />,
        loader: fetchAdminLoader,
    },
    {
        path: "/device-limits/",
        element: <DeviceLimits />,
        errorElement: <Login />,
        loader: fetchAdminLoader,
    },
    {
        path: "/plans/",
        element: <OwnerOnly><Plans /></OwnerOnly>,
        errorElement: <Login />,
        loader: fetchAdminLoader,
    },
    {
        path: "/settings/",
        element: <OwnerOnly><Settings /></OwnerOnly>,
        errorElement: <Login />,
        loader: fetchAdminLoader,
    },
    {
        path: "/audit-logs/",
        element: <AuditLogs />,
        errorElement: <Login />,
        loader: fetchAdminLoader,
    },
]);
