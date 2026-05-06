export default function AdminPage() {
  return (
    <section>
      <h1>Admin</h1>
      <p>
        Admins manage users, roles, and tech permissions. Admins do
        <strong> not </strong> control financial visibility — that authority
        belongs to the CFO via the Visibility Policy.
      </p>
      <ul>
        <li>POST /api/v1/admin/users</li>
        <li>GET /api/v1/admin/users</li>
        <li>GET /api/v1/admin/whoami</li>
      </ul>
    </section>
  );
}
