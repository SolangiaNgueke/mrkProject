from django.core.signing import BadSignature, SignatureExpired
from django.http import HttpResponse
from django.views import View
from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.response import Response

from .emails import envoyer_verification, lire_jeton
from .serializers import RegisterSerializer, UserSerializer


class RegisterView(generics.CreateAPIView):
    """POST /api/auth/register/  -> crée un compte (citoyen) et renvoie un token."""

    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        envoyer_verification(user)   # lien de confirmation de l'adresse
        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {"token": token.key, "user": UserSerializer(user).data},
            status=status.HTTP_201_CREATED,
        )


class LoginView(ObtainAuthToken):
    """POST /api/auth/login/  {username, password}  -> token + profil."""

    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": UserSerializer(user).data})


class MeView(generics.RetrieveAPIView):
    """GET /api/auth/me/  -> profil de l'utilisateur connecté."""

    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


def _page(titre, message, couleur):
    """Petite page HTML de retour après clic sur le lien de confirmation."""
    return HttpResponse(
        f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
        <title>{titre}</title></head>
        <body style="font-family:system-ui;background:#f3f4f6;margin:0;
                     display:flex;align-items:center;justify-content:center;height:100vh">
          <div style="background:#fff;padding:32px 40px;border-radius:12px;
                      box-shadow:0 1px 6px rgba(0,0,0,.1);text-align:center;max-width:420px">
            <h1 style="color:{couleur};font-size:20px;margin:0 0 10px">{titre}</h1>
            <p style="color:#4b5563;font-size:14px;line-height:1.5">{message}</p>
            <a href="http://localhost:5500/index.html"
               style="display:inline-block;margin-top:14px;padding:9px 16px;background:#2563eb;
                      color:#fff;text-decoration:none;border-radius:7px;font-size:14px">
              Retour à la carte</a>
          </div></body></html>"""
    )


class VerifyEmailView(View):
    """GET /api/auth/verify-email/?token=...  -> confirme l'adresse email."""

    def get(self, request):
        from django.contrib.auth import get_user_model

        token = request.GET.get("token", "")
        try:
            user_id = lire_jeton(token)
        except SignatureExpired:
            return _page("Lien expiré", "Ce lien de confirmation a plus de 48 heures. "
                         "Demandez un nouvel envoi depuis la carte.", "#b45309")
        except (BadSignature, ValueError):
            return _page("Lien invalide", "Ce lien de confirmation n'est pas valide.", "#b91c1c")

        User = get_user_model()
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return _page("Compte introuvable", "Ce compte n'existe plus.", "#b91c1c")

        if not user.email_verified:
            user.email_verified = True
            user.save(update_fields=["email_verified"])
        return _page("Adresse confirmée", "Merci ! Votre adresse email est vérifiée : "
                     "vous recevrez désormais les notifications de suivi.", "#15803d")


class ResendVerificationView(APIView):
    """POST /api/auth/resend-verification/  -> renvoie le lien de confirmation."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        if not user.email:
            return Response(
                {"detail": "Aucune adresse email enregistrée sur ce compte."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if user.email_verified:
            return Response({"detail": "Votre adresse est déjà vérifiée."})
        envoyer_verification(user)
        return Response({"detail": f"Lien de confirmation renvoyé à {user.email}."})