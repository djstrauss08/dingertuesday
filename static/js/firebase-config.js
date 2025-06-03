// Import the functions you need from the SDKs you need
import { initializeApp } from "firebase/app";
import { getAuth, signInWithEmailAndPassword, createUserWithEmailAndPassword, signOut, onAuthStateChanged } from "firebase/auth";
import { getAnalytics } from "firebase/analytics";

// Your web app's Firebase configuration
const firebaseConfig = {
  apiKey: "AIzaSyDmw8XHzlBo2lB8X1rzyjQbgd5aRkRilv0",
  authDomain: "dingertuesday-18a26.firebaseapp.com",
  projectId: "dingertuesday-18a26",
  storageBucket: "dingertuesday-18a26.firebasestorage.app",
  messagingSenderId: "649904034928",
  appId: "1:649904034928:web:03010b19c057bf4bcc10b5",
  measurementId: "G-H0ZR5N8SS8"
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const analytics = getAnalytics(app);

export { auth, signInWithEmailAndPassword, createUserWithEmailAndPassword, signOut, onAuthStateChanged }; 