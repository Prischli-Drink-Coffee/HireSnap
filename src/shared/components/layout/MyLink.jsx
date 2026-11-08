import React from "react";
import { NavLink } from "react-router-dom";
import styles from "shared/components/layout/link.module.css";

const MyLink = ({ to, name, onClick }) => (
  <NavLink
    onClick={onClick}
    className={({ isActive }) =>
      isActive ? `${styles.Link__active} ${styles.Link}` : styles.Link
    }
    to={to}
  >
    {name}
  </NavLink>
);

export default MyLink;
